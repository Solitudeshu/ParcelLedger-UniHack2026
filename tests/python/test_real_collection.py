import copy
import json

import numpy as np
import pytest
from PIL import Image

from scripts.real_data import build, review


def collection(tmp_path, count=5):
    (tmp_path/"rights.json").write_text(json.dumps({"source":"TEAM_OWNED","permission":"RESEARCH_AND_COMMERCIAL","declared_by":"test"}))
    rng = np.random.default_rng(9)
    for i in range(count):
        for stage in ("dispatch", "receipt"):
            folder = tmp_path/f"box-{i}"/stage
            folder.mkdir(parents=True)
            Image.fromarray(rng.integers(0,255,(32,32,3),dtype=np.uint8)).save(folder/"image.png")
    output = tmp_path/"manifest.json"
    build(tmp_path, output, "test-collection", source_kind="SOFTWARE_FIXTURE")
    return json.loads(output.read_text())


def test_collection_never_invents_labels_or_real_validation(tmp_path):
    manifest = collection(tmp_path)
    assert manifest["physical_box_count"] == 0
    assert all(row["correspondence"] == "UNKNOWN" for row in manifest["pairs"])
    with pytest.raises(ValueError, match="MANIFEST_EXISTS"):
        build(tmp_path, tmp_path/"manifest.json", "test-collection")
    submission = {"manifest_sha256":manifest["manifest_sha256"],"annotator_id":"a","annotations":manifest["pairs"]}
    result = review(manifest, [submission, {**submission,"annotator_id":"b"}])
    assert result["status"] == "REQUIRES_ADJUDICATION" and result["unresolved"]
    with pytest.raises(ValueError, match="INDEPENDENT"):
        review(manifest, [submission, submission])


def test_reviews_cannot_rewrite_identity_or_ignore_disagreement(tmp_path):
    manifest = collection(tmp_path)
    rows = copy.deepcopy(manifest["pairs"])
    for row in rows:
        row["correspondence"] = "MATCH" if row["query_lineage_id"] == row["reference_lineage_id"] else "WRONG_OBJECT"
        row["sufficiency"] = "INSUFFICIENT"
    a = {"manifest_sha256":manifest["manifest_sha256"],"annotator_id":"a","annotations":rows}
    b = copy.deepcopy(a); b["annotator_id"]="b"
    b["annotations"][0]["correspondence"] = "UNKNOWN"
    result = review(manifest,[a,b])
    assert result["conflicts"] == [rows[0]["pair_id"]]
    b["manifest_sha256"] = "0"*64
    with pytest.raises(ValueError, match="MANIFEST_MISMATCH"):
        review(manifest,[a,b])
