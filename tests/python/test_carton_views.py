import numpy as np

from ml.data.carton_views import box_views, corpus_split, face_texture, image_hash, pair_label
from ml.features.geometry import describe, scalar_features


def test_generator_labels_geometry_and_nuisances():
    queries, references = box_views(26)
    assert len({image_hash(v.image) for v in queries + references}) == 10
    for query in queries:
        labels = [pair_label(query, ref) for ref in references]
        assert sum(label["same_region"] for label in labels) == 2
        if query.condition != "clean":
            assert not any(label["sufficient"] for label in labels)
        else:
            assert any(label["sufficient"] for label in labels)


def test_generator_split_and_seed_are_reproducible():
    assert corpus_split(30) == corpus_split(30)
    assert set(corpus_split(30)["lineages"].values()) == {"train", "validation", "test"}
    assert image_hash(face_texture(1, 1)) == image_hash(face_texture(1, 1))
    assert image_hash(face_texture(1, 1)) != image_hash(face_texture(1, 2))


def test_geometry_has_no_label_input_and_handles_blank_images():
    query = describe(np.zeros((64, 64, 3), np.uint8))
    features = scalar_features(query, query, 1.)
    assert features.shape == (12,)
    assert features[1] == features[4] == features[5] == 0
    assert np.isnan(features[3]) and np.isnan(features[-1])
