import pytest

from ml.evaluate_roma_negatives import wrong_object_gallery


def test_negative_gallery_is_deterministic_and_excludes_the_query_object():
    lineages = [f"object{i}" for i in range(25) for _ in range(4)]
    for index in range(len(lineages)):
        selected = wrong_object_gallery(lineages, index)
        assert selected == wrong_object_gallery(lineages, index)
        assert len({lineages[i] for i in selected}) == 4
        assert all(lineages[i] != lineages[index] for i in selected)
    with pytest.raises(ValueError, match="NEED_FOUR_OTHER_OBJECTS"):
        wrong_object_gallery(["a", "a", "b", "c"], 0)
