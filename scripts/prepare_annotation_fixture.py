"""Generate UI test images only; never physical-box evaluation data."""
import json
from pathlib import Path

import numpy as np
from PIL import Image

from scripts.real_data import build


def main():
    root = Path(".local/annotation-fixture/photos")
    root.mkdir(parents=True, exist_ok=True)
    (root/"rights.json").write_text(json.dumps({"source":"TEAM_OWNED", "permission":"RESEARCH_AND_COMMERCIAL", "declared_by":"software-fixture-generator"}))
    for i in range(5):
        for stage in ("dispatch", "receipt"):
            folder = root/f"box-{i}"/stage
            folder.mkdir(parents=True, exist_ok=True)
            rng = np.random.default_rng(i*13+(stage == "receipt"))
            Image.fromarray(rng.integers(30,240,(180,240,3),dtype=np.uint8)).save(folder/"image.png")
    output = root.parent/"manifest-v1.json"
    if not output.exists():
        build(root, output, "annotation-ui-fixture", source_kind="SOFTWARE_FIXTURE")
    print("Annotation UI fixtures ready; physical-box count remains zero.")


if __name__ == "__main__":
    main()
