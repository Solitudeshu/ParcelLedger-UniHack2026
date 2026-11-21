"""Verify collected change-head weights against Kaggle logits on selection probes."""
import argparse
import json
from pathlib import Path

from ml.inference.change import ChangePredictor


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = ChangePredictor(args.folder).parity()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
