import pytest

from ml.data.parcel3d_lineage import parse_lineage


def config(obj, category=0, name="boxA"):
    return {"SCENARIO": {"ID": "scenario", "FLAG": "train", "CATEGORY_ID": category,
                          "MODEL": {"NAME": name, "OBJ": obj}}, "RENDER": {"VIEWS": 1}}


def test_deformation_stays_in_the_source_object_lineage():
    normal = parse_lineage(config("google_scanned_objects/boxA/meshes/scaled/model_05.obj"), "scenario", "train", 0)
    damaged = parse_lineage(config("google_scanned_objects/boxA/meshes/deformed/falling/run.obj", 1), "scenario", "train", 1)
    assert normal["lineage_id"] == damaged["lineage_id"]
    assert normal["source_mesh"] != damaged["source_mesh"]


@pytest.mark.parametrize("obj", ["../boxA/model.obj", "/data/model.obj", "C:/data/model.obj", "google_scanned_objects/boxB/meshes/model.obj"])
def test_unknown_or_unsafe_lineage_fails_closed(obj):
    with pytest.raises(ValueError):
        parse_lineage(config(obj), "scenario", "train", 0)


def test_mismatched_metadata_is_not_silently_used():
    with pytest.raises(ValueError, match="MISMATCH"):
        parse_lineage(config("google_scanned_objects/boxA/meshes/model.obj"), "wrong", "train", 0)
