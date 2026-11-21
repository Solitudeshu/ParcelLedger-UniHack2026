"""Freeze -> predict from images alone -> score finalized human labels."""
import argparse
import json
import platform
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps

from ml.eval.real_boxes import evaluate, verify_hash
from scripts.real_data import canonical, digest


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value, hash_field=None):
    if hash_field:
        value[hash_field] = digest(canonical(value))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)


def identity(model):
    paths = [Path("services/worker/baseline.py"), Path("services/api/images.py"), Path("services/api/schemas.py"),
             Path("scripts/evaluate_real.py"), Path("ml/eval/real_boxes.py")]
    paths += sorted(Path("ml/features").glob("*.py"))
    paths += sorted(Path("ml/inference").glob("*.py"))
    if model == "ranker":
        paths += [Path("artifacts/models/reference-ranker")/name for name in ("ranker.npz","normalizer.json","model-card.json","export-parity.npz")]
    if model == "change":
        paths += [Path("ml/train/change_head.py")]
        paths += [Path("artifacts/models/change-head")/name for name in ("change-head.pt","model-card.json","export-parity.npz")]
    return {path.as_posix():digest(path.read_bytes()) for path in paths}


def freeze(manifest, model, split):
    if model not in {"baseline","ranker","change"} or split not in {"validation","test"}:
        raise ValueError("INVALID_EVALUATION_PROTOCOL")
    verify_hash(manifest, "manifest_sha256")
    return {"version":"real-evaluation-protocol-v1", "manifest_sha256":manifest["manifest_sha256"],
            "model":model, "split":split, "identity":identity(model), "created_at":datetime.now(timezone.utc).isoformat(),
            "candidate_policy":"All manifest pairs for each query; no labels consumed at inference.",
            "threshold_policy":"Use frozen source/model-card thresholds unchanged; no test tuning.",
            "bootstrap":{"cluster":"query_lineage_id","replicates":1000,"seed":26},
            "annotation_coordinate_frame":"EXIF-oriented, normalized [0,1]"}


def predict(manifest, protocol, root):
    from services.worker.baseline import analyze_images
    verify_hash(manifest,"manifest_sha256")
    verify_hash(protocol,"protocol_sha256")
    if protocol["manifest_sha256"] != manifest["manifest_sha256"] or identity(protocol["model"]) != protocol["identity"]:
        raise ValueError("FROZEN_PROTOCOL_CHANGED")
    root = Path(root).resolve()
    if digest((root/"rights.json").read_bytes()) != manifest["rights_declaration_sha256"]:
        raise ValueError("RIGHTS_DECLARATION_CHANGED")
    assets = {a["id"]:a for a in manifest["assets"]}
    def load(asset_id):
        asset = assets[asset_id]
        path = (root/asset["path"]).resolve()
        if not path.is_relative_to(root) or path.stat().st_size > 20*1024*1024:
            raise ValueError("IMAGE_PATH_OR_SIZE_INVALID")
        if digest(path.read_bytes()) != asset["sha256"]:
            raise ValueError("IMAGE_CHECKSUM_MISMATCH")
        with Image.open(path) as image:
            if image.width*image.height > 40_000_000 or getattr(image,"n_frames",1) != 1:
                raise ValueError("IMAGE_DECODE_LIMIT")
            image = ImageOps.exif_transpose(image).convert("RGB")
            if image.size != (asset["width"],asset["height"]):
                raise ValueError("IMAGE_DIMENSION_MISMATCH")
            return image.copy()
    advisor = change = None
    if protocol["model"] == "ranker":
        from ml.features.dino import DinoEncoder
        from ml.inference.ranker import ShadowAdvisor
        advisor = ShadowAdvisor("artifacts/models/reference-ranker",DinoEncoder(cache_dir=Path(".local/dino-cache")))
    elif protocol["model"] == "change":
        from ml.inference.change import ChangePredictor
        change = ChangePredictor("artifacts/models/change-head")
    candidates = defaultdict(list)
    for row in manifest["pairs"]:
        if row["split"] == protocol["split"]:
            candidates[row["query_id"]].append(row["reference_id"])
    if not candidates:
        raise ValueError("EMPTY_EVALUATION_SPLIT")
    predictions = []
    for query_id, ids in candidates.items():
        if not 1 <= len(ids) <= 40 or len(set(ids)) != len(ids):
            raise ValueError("CANDIDATE_COUNT_OUTSIDE_PRODUCT_LIMITS")
        query = load(query_id)
        started = time.perf_counter()
        if advisor:
            result = advisor.compare(query,((key,load(key)) for key in ids))
            ranking = [row["asset_id"] for row in result["candidates"]]
            accepted = result["synthetic_threshold_accepts"]
            state, polygons = "NOT_SUPPORTED", []
        else:
            result = analyze_images(query_id,query,((key,load(key)) for key in ids))
            ranking = [row["asset_id"] for row in result["candidates"]]
            accepted = result["match"]["status"] == "MATCHED_SUFFICIENT"
            state = "EVALUATED" if accepted else "ABSTAIN"
            polygons = [r["query_polygon"] for r in result["observations"] if r["state"] == "VISIBLE_CHANGE"]
            if change:
                observed = change.compare(query,load(ranking[0]) if accepted else None,result["match"])
                state, polygons = observed["status"], observed["query_polygons"]
        predictions.append({"query_id":query_id, "ranking":ranking, "selected_reference_id":ranking[0],
            "accepted":accepted,"change":{"state":state,"polygons":[[[v/10000 for v in point] for point in polygon] for polygon in polygons]},
            "elapsed_seconds":time.perf_counter()-started})
    if identity(protocol["model"]) != protocol["identity"]:
        raise ValueError("MODEL_CHANGED_DURING_INFERENCE")
    return {"version":"real-predictions-v1", "manifest_sha256":manifest["manifest_sha256"],
            "protocol_sha256":protocol["protocol_sha256"], "source_kind":manifest["source_kind"],
            "runtime":{"python":platform.python_version(),"platform":platform.platform()}, "queries":predictions}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command",required=True)
    for command in ("freeze","predict","score"):
        child = sub.add_parser(command)
        child.add_argument("--manifest",required=True)
        child.add_argument("--output",required=True)
        if command == "freeze":
            child.add_argument("--model",choices=("baseline","ranker","change"),required=True)
            child.add_argument("--split",choices=("validation","test"),default="test")
        else:
            child.add_argument("--protocol",required=True)
        if command == "predict":
            child.add_argument("--root",required=True)
        if command == "score":
            child.add_argument("--labels",required=True)
            child.add_argument("--predictions",required=True)
    args = parser.parse_args()
    manifest = read(args.manifest)
    if args.command == "freeze":
        result, field = freeze(manifest,args.model,args.split), "protocol_sha256"
    elif args.command == "predict":
        result, field = predict(manifest,read(args.protocol),args.root), "predictions_sha256"
    else:
        result, field = evaluate(manifest,read(args.labels),read(args.predictions),read(args.protocol)), "evaluation_sha256"
    save(args.output,result,field)
    print(json.dumps({"command":args.command,"output":args.output,"sha256":result[field],"status":result.get("status","ARTIFACT_CREATED")}))
