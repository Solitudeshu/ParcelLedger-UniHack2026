import json
from pathlib import Path

import pytest

from services.api.canonical import canonical_bytes, document_hash


@pytest.mark.parametrize(
    "vector",
    json.loads(Path("tests/fixtures/canonical-v1.json").read_text(encoding="utf-8")),
    ids=lambda v: v["name"],
)
def test_golden_vectors(vector):
    assert canonical_bytes(vector["document"]).decode() == vector["canonical"]
    assert document_hash(vector["document"]) == vector["sha256"]


@pytest.mark.parametrize(
    "value", [{"x": float("nan")}, {"x": float("inf")}, {"x": 2**60}, {"é": 1, "e\u0301": 2}]
)
def test_ambiguous_json_rejected(value):
    with pytest.raises(ValueError):
        canonical_bytes(value)
