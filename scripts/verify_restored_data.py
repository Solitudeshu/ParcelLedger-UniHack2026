"""Read a disposable restored DB and preserve/check every accepted evidence object."""
import argparse
import json
import re
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from services.api.canonical import bytes_hash, document_hash
from services.api.config import settings
from services.api.database import Asset, CaptureSession, Report
from services.api.storage import storage


def verify(db_name, folder):
    if not re.fullmatch(r"kienchung_restore_[0-9a-f]{12}", db_name):
        raise ValueError("RESTORE_DATABASE_NAME_INVALID")
    folder = Path(folder).resolve()
    allowed = Path(".local/backups").resolve()
    if not folder.is_relative_to(allowed) or folder == allowed:
        raise ValueError("BACKUP_PATH_INVALID")
    engine = create_engine(make_url(settings().database_url).set(database=db_name))
    inventory = []
    with Session(engine) as db:
        for asset in db.scalars(select(Asset).where(Asset.status.in_(["UPLOADED", "READY"]))):
            raw = storage().get(asset.object_key)
            if len(raw) != asset.byte_size or bytes_hash(raw) != asset.sha256:
                raise ValueError("RESTORED_EVIDENCE_CHECKSUM_MISMATCH")
            target = folder / "evidence" / asset.id
            if not target.resolve().is_relative_to(folder):
                raise ValueError("EVIDENCE_PATH_INVALID")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            if bytes_hash(target.read_bytes()) != asset.sha256:
                raise ValueError("BACKUP_FILE_CHECKSUM_MISMATCH")
            inventory.append({"asset_id": asset.id, "object_key": asset.object_key, "sha256": asset.sha256, "bytes": len(raw)})
        captures = db.scalars(select(CaptureSession).where(CaptureSession.status == "SEALED")).all()
        for capture in captures:
            if document_hash(capture.manifest) != capture.manifest_hash:
                raise ValueError("RESTORED_MANIFEST_CHECKSUM_MISMATCH")
        reports = db.scalars(select(Report)).all()
        for report in reports:
            if document_hash(report.document) != report.canonical_hash:
                raise ValueError("RESTORED_REPORT_CHECKSUM_MISMATCH")
    engine.dispose()
    (folder / "evidence-inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    return {"status": "PASS", "asset_count": len(inventory), "original_bytes": sum(a["bytes"] for a in inventory),
            "sealed_manifest_count": len(captures), "report_count": len(reports), "database": db_name}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-name", required=True)
    parser.add_argument("--folder", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.db_name, args.folder)))
