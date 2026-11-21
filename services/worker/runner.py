import argparse
import logging
import threading
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select, update

from services.api.canonical import bytes_hash, document_hash
from services.api.chain import assert_report_editable
from services.api.database import Asset, Job, Report, SessionLocal, Shipment, now
from services.api.images import decode_image
from services.api.storage import storage
from services.api.transactions import event
from services.worker.baseline import (
    BASELINE_CONFIG,
    BASELINE_VERSION,
    SOURCE_SHA256,
    analyze_images,
)

logger = logging.getLogger("kienchung.worker")
LEASE_SECONDS = 90
MAX_ATTEMPTS = 3


def claim_job():
    with SessionLocal() as db, db.begin():
        # Reconcile terminal failures, including jobs left by an earlier worker.
        active = select(Job.id).where(Job.shipment_id == Shipment.id,
                                      Job.status.in_(["RUNNING", "QUEUED"])).exists()
        failed = select(Job.id).where(Job.shipment_id == Shipment.id, Job.status == "FAILED").exists()
        for shipment in db.scalars(select(Shipment).where(Shipment.status == "ANALYZING",
                                                           ~active, failed).with_for_update(skip_locked=True)):
            shipment.status = "REVIEW" if shipment.report_version else "RECEIVED"
            shipment.version += 1
        expired = and_(Job.status == "RUNNING", Job.lease_until < now())
        exhausted = db.scalars(
            select(Job)
            .where(expired, Job.attempts >= MAX_ATTEMPTS)
            .with_for_update(skip_locked=True)
        ).all()
        for job in exhausted:
            job.status = "FAILED"
            job.error = {
                "code": "RETRY_EXHAUSTED",
                "message": "Đã vượt số lần thử. Cần kiểm tra dịch vụ xử lý.",
            }
            job.lease_owner = None
            job.lease_until = None
        job = db.scalar(
            select(Job)
            .where(or_(Job.status == "QUEUED", expired), Job.attempts < MAX_ATTEMPTS)
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            return None
        owner = str(uuid4())
        job.status, job.lease_owner = "RUNNING", owner
        job.lease_until = now() + timedelta(seconds=LEASE_SECONDS)
        job.attempts += 1
        job.error = None
        job.progress = "Đang chọn ảnh tham chiếu và kiểm tra mức đủ chi tiết"
        return job.id, owner, job.inputs


def heartbeat(job_id, owner, stop):
    while not stop.wait(20):
        try:
            with SessionLocal() as db, db.begin():
                result = db.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "RUNNING", Job.lease_owner == owner)
                    .values(lease_until=now() + timedelta(seconds=LEASE_SECONDS))
                )
                if result.rowcount != 1:
                    stop.set()
        except Exception:
            logger.warning("Heartbeat unavailable for job %s", job_id)


def load_image(asset_id, shipment_id):
    with SessionLocal() as db:
        asset = db.get(Asset, asset_id)
        if not asset or asset.shipment_id != shipment_id or asset.status != "READY":
            raise ValueError("INPUT_ASSET_INVALID")
        raw = storage().get(asset.object_key)
        if bytes_hash(raw) != asset.sha256:
            raise ValueError("INPUT_CHECKSUM_MISMATCH")
        return decode_image(raw, asset.mime_type)


def finish(job_id, owner, result):
    with SessionLocal() as db, db.begin():
        existing = db.get(Job, job_id)
        shipment = db.scalar(
            select(Shipment).where(Shipment.id == existing.shipment_id).with_for_update()
        )
        job = db.scalar(
            select(Job)
            .where(Job.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job.lease_owner != owner or job.status != "RUNNING" or job.lease_until < now():
            return False  # A worker with an expired lease must never publish output.
        if db.scalar(select(Report).where(Report.job_id == job_id)):
            return False
        assert_report_editable(db, shipment)
        previous = db.scalar(
            select(Report)
            .where(Report.shipment_id == shipment.id)
            .order_by(Report.version.desc())
            .limit(1)
        )
        document = {
            "schema_version": "report-v1",
            "shipment_id": shipment.id,
            "case_id": shipment.case_id,
            "reference": shipment.reference,
            "title": shipment.title,
            "version": shipment.report_version + 1,
            "parent_report_hash": previous.canonical_hash if previous else None,
            "dispatch_manifest_hashes": job.inputs["reference_manifest_hashes"],
            "receipt_manifest_hash": job.inputs["query_manifest_hash"],
            "model_run_id": job.id,
            "model_version": job.inputs["model_version"],
            "selection_method": job.inputs.get("selection_method", "AUTOMATIC"),
            "inference_kind": result["inference_kind"],
            "research_advice": result.get("research_advice"),
            "research_change": result.get("research_change"),
            "research_transformer": result.get("research_transformer"),
            "recapture_instructions": result.get("recapture_instructions", []),
            "report_composition": result.get("report_composition"),
            "match": result["match"],
            "observations": result["observations"],
            "user_notes": "",
            "evidence_asset_ids": sorted(
                {job.inputs["query_asset_id"], *job.inputs["reference_asset_ids"]}
            ),
            "created_by": "worker",
            "created_at": now().isoformat(),
            "scope": "Ghi nhận tình trạng bên ngoài nhìn thấy trong ảnh; không xác định nguyên nhân, trách nhiệm hoặc số tiền bồi hoàn.",
        }
        report = Report(
            shipment_id=shipment.id,
            version=document["version"],
            job_id=job.id,
            parent_id=previous.id if previous else None,
            document=document,
            canonical_hash=document_hash(document),
            created_by="worker",
        )
        db.add(report)
        db.flush()
        job.status, job.progress = "SUCCEEDED", "Đã tạo bản nháp để hai bên kiểm tra"
        job.result = {**result, "report_id": report.id, "report_version": report.version}
        job.result_hash = document_hash(job.result)
        job.lease_owner = job.lease_until = None
        shipment.status = "REVIEW"
        shipment.report_version += 1
        shipment.version += 1
        event(
            db,
            shipment.id,
            "worker",
            "REPORT_DRAFTED",
            {"job_id": job.id, "version": report.version, "report_hash": report.canonical_hash},
        )
        return True


def run_once():
    claimed = claim_job()
    if not claimed:
        return False
    job_id, owner, inputs = claimed
    stop = threading.Event()
    thread = threading.Thread(target=heartbeat, args=(job_id, owner, stop), daemon=True)
    thread.start()
    try:
        if inputs["model_version"] != BASELINE_VERSION or inputs["config_sha256"] != document_hash(
            BASELINE_CONFIG
        ):
            raise ValueError("MODEL_VERSION_MISMATCH")
        if inputs.get("baseline_source_sha256", SOURCE_SHA256) != SOURCE_SHA256:
            raise ValueError("MODEL_VERSION_MISMATCH")
        query = load_image(inputs["query_asset_id"], inputs["shipment_id"])
        def references():
            for asset_id in inputs["reference_asset_ids"]:
                yield asset_id, load_image(asset_id, inputs["shipment_id"])
        result = analyze_images(
            inputs["query_asset_id"], query, references(), inputs["query_region"], run_id=job_id
        )
        from services.worker.recapture import instructions
        result["recapture_instructions"] = instructions(result["match"])
        if inputs.get("shadow_checkpoint_sha256"):
            from services.worker.shadow import evaluate
            try:
                result["research_advice"] = evaluate(query, references(), inputs["query_region"], inputs["shadow_checkpoint_sha256"], inputs.get("shadow_bundle_identity"))
            except Exception:
                logger.warning("Shadow model unavailable for job %s", job_id)
                result["research_advice"] = {"status": "UNAVAILABLE", "mode": "SHADOW_ONLY",
                    "checkpoint_sha256": inputs["shadow_checkpoint_sha256"], "changes_product_decision": False}
        if inputs.get("shadow_change_identity"):
            from services.worker.change_shadow import evaluate as evaluate_change
            try:
                reference_id = result["match"]["reference_asset_id"]
                reference = load_image(reference_id, inputs["shipment_id"]) if reference_id else None
                result["research_change"] = evaluate_change(query, reference, result["match"], inputs["shadow_change_identity"])
            except Exception:
                logger.warning("Change shadow model unavailable for job %s", job_id)
                result["research_change"] = {"status": "UNAVAILABLE", "mode": "SHADOW_ONLY",
                    **inputs["shadow_change_identity"], "changes_product_decision": False}
        if inputs.get("research_transformer_identity"):
            from services.worker.transformer_shadow import evaluate as evaluate_transformer
            try:
                result["research_transformer"] = evaluate_transformer(
                    inputs["query_asset_id"], query, references(), inputs["query_region"],
                    inputs["research_transformer_identity"])
            except Exception:
                logger.warning("Final Transformer advisor unavailable for job %s", job_id)
                result["research_transformer"] = {
                    "status": "UNAVAILABLE", "mode": "SHADOW_ONLY", "changes_product_decision": False,
                    **inputs["research_transformer_identity"], "query_asset_id": inputs["query_asset_id"],
                    "query_region": inputs["query_region"], "query_polygons": [], "reference_asset_id": None}
        elif inputs.get("research_transformer_requested"):
            result["research_transformer"] = {
                "status": "UNAVAILABLE", "mode": "SHADOW_ONLY", "changes_product_decision": False,
                "query_asset_id": inputs["query_asset_id"], "query_region": inputs["query_region"],
                "query_polygons": [], "reference_asset_id": None}
        if result.get("research_transformer"):
            result["research_transformer"]["requested_reference_asset_id"] = (
                inputs["reference_asset_ids"][0] if inputs["selection_method"] == "USER_SELECTED" else None)
        from services.worker.report_composer import compose
        result["observations"], result["report_composition"] = compose(result["observations"], inputs.get("composition_config_sha256"))
        finish(job_id, owner, result)
    except Exception as exc:
        # Persist safe error categories, never raw storage/provider messages or credentials.
        known = {"MODEL_VERSION_MISMATCH", "INPUT_ASSET_INVALID", "INPUT_CHECKSUM_MISMATCH"}
        code = (
            str(exc) if isinstance(exc, ValueError) and str(exc) in known else "MODEL_UNAVAILABLE"
        )
        with SessionLocal() as db, db.begin():
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job.lease_owner == owner and job.status == "RUNNING":
                job.status = "FAILED" if job.attempts >= MAX_ATTEMPTS or code in known else "QUEUED"
                job.error = {
                    "code": code,
                    "message": "Phân tích chưa hoàn tất; vui lòng kiểm tra hoặc thử lại.",
                }
                job.progress = "Chưa thể hoàn tất phân tích"
                job.lease_owner = job.lease_until = None
        logger.warning("Job %s failed: %s", job_id, code)
    finally:
        stop.set()
        thread.join(timeout=2)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.once:
        run_once()
        return
    while True:
        try:
            worked = run_once()
        except Exception:
            logger.warning("Worker dependencies unavailable; retrying")
            worked = False
        time.sleep(0.5 if worked else 3)


if __name__ == "__main__":
    main()
