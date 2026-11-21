import json

import pytest
from PIL import Image

from ml.data.parcel3d_audit import audit, find_annotations, within_root


def test_audit_mount_reads_without_copying_images(tmp_path):
    source, output = tmp_path / "input", tmp_path / "output"
    source.mkdir()
    for index, split in enumerate(("train", "validation", "test")):
        (source / split).mkdir()
        Image.new("RGB", (32, 32), (180, 140, 100)).save(source / split / "image.png")
        document = {
            "images": [
                {"id": index, "file_name": f"{split}/image.png", "mesh_id": f"mesh-{index}"}
            ],
            "annotations": [],
            "categories": [{"id": 0, "name": "parcel"}],
        }
        (source / f"parcel3d_{split}.json").write_text(json.dumps(document))
    result = audit(source, output)
    assert result["mount_read_gate"] == "PASS"
    assert result["splits"]["train"]["group_split_verified"]
    assert not list(output.rglob("*.png"))
    assert result["commercial_license_gate"] == "HOLD_ORIGINAL_RENDERS"


@pytest.mark.parametrize(
    "filename", ["../outside.png", "/etc/passwd", "C:/secret", "a/../../outside"]
)
def test_annotation_cannot_escape_mount(tmp_path, filename):
    with pytest.raises(ValueError):
        within_root(tmp_path, filename)


def test_duplicate_mount_aliases_are_verified_by_content(tmp_path):
    for alias in ("parcel3d/parcel3d", "datasets/owner/parcel3d/parcel3d"):
        root = tmp_path / alias
        root.mkdir(parents=True)
        for split in ("train", "validation", "test"):
            (root / f"parcel3d_{split}.json").write_text('{"images": []}')
    found = find_annotations(tmp_path)
    assert found["train"].parent == tmp_path / "parcel3d/parcel3d"
    (tmp_path / "datasets/owner/parcel3d/parcel3d/parcel3d_train.json").write_text(
        '{"images": [1]}'
    )
    with pytest.raises(RuntimeError, match="DIFFERENT_CONTENT"):
        find_annotations(tmp_path)
