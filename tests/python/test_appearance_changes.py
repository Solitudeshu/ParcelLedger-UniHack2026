import numpy as np

from ml.data.appearance_changes import example, split_manifest


def test_change_masks_are_independent_of_lighting_and_have_ignore_regions():
    for scenario in range(4):
        features,target,valid=example(1,scenario)
        assert features.shape == (10,128,128)
        assert target.shape == valid.shape == (1,128,128)
        assert np.isfinite(features).all()
        assert np.array_equal(features[-1],valid[0])
        assert bool(target.any()) == bool(scenario%2)
        assert valid.mean()<1
        if scenario>=2:assert valid.mean()<.9
        assert np.array_equal(features,example(1,scenario)[0])


def test_change_split_uses_boxes_not_image_rows():
    split=split_manifest()
    assert len(split["lineages"])==240
    assert set(split["lineages"].values())=={"train","validation","test"}
