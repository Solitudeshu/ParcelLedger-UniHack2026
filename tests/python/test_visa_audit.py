import io
import tarfile

import numpy as np
import pytest
from PIL import Image

from ml.data.visa_audit import audit_archive, normalized_member


def test_decoded_duplicate_cross_split_is_not_training_ready(tmp_path):
    array = np.full((16, 16, 3), 180, np.uint8)
    archive = tmp_path / "sample.tar"
    with tarfile.open(archive, "w") as tar:
        for index, split in enumerate(("train", "test")):
            stream = io.BytesIO()
            Image.fromarray(array).save(stream, format="PNG", compress_level=index * 9)
            raw = stream.getvalue()
            member = tarfile.TarInfo(f"./VisA/object/{split}.png")
            member.size = len(raw)
            tar.addfile(member, io.BytesIO(raw))
    csv = (b"object,split,label,image,mask\n"
           b"object,train,normal,object/train.png,\n"
           b"object,test,normal,object/test.png,\n")
    result = audit_archive(archive, csv, tmp_path / "audit", expected_count=2)
    assert result["counts"] == {"train": 1, "test": 1}
    assert result["cross_split_duplicate_groups"] == 1
    assert result["duplicate_groups_by_hash_type"] == {"rgb_sha256": 1}
    assert result["training_ready"] is False
    assert len((tmp_path / "audit/images.jsonl").read_text().splitlines()) == 2


@pytest.mark.parametrize("path", ["../../file", "/tmp/file", "C:/file", "x\\..\\file"])
def test_archive_paths_cannot_escape(path):
    with pytest.raises(ValueError, match="UNSAFE_ARCHIVE_MEMBER"):
        normalized_member(path)


def test_missing_image_is_not_a_successful_audit(tmp_path):
    archive = tmp_path / "empty.tar"
    with tarfile.open(archive, "w"):
        pass
    csv = b"object,split,label,image,mask\nx,train,normal,x/missing.png,\n"
    with pytest.raises(ValueError, match="OFFICIAL_IMAGES_MISSING_FROM_ARCHIVE"):
        audit_archive(archive, csv, tmp_path / "audit", expected_count=1)
