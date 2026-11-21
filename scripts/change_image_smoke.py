"""Image-path integration check; procedural fixtures are not real accuracy evidence."""
import json
from pathlib import Path

from PIL import Image

from ml.inference.change import ChangePredictor
from services.worker.baseline import analyze_images


def main():
    model = ChangePredictor("artifacts/models/change-head")
    reference = Image.open("tests/fixtures/images/dispatch.png").convert("RGB")
    results = []
    for name in ("changed", "lighting", "low-detail", "no-reference"):
        query = Image.open(f"tests/fixtures/images/{name}.png").convert("RGB")
        baseline = analyze_images(name, query, [("dispatch", reference)], run_id="smoke")
        prediction = model.compare(query, reference, baseline["match"])
        assert prediction["changes_product_decision"] is False
        if baseline["match"]["status"] != "MATCHED_SUFFICIENT":
            assert prediction["status"] == "ABSTAIN"
        else:
            assert prediction["status"] == "EVALUATED"
        results.append({"fixture": name, "baseline_status": baseline["match"]["status"], "prediction": prediction})
    report = {"status": "PASS", "scope": "PROCEDURAL_FIXTURE_PLUMBING_ONLY", "parity": model.parity(), "scenarios": results}
    Path("artifacts/evaluation/change-image-smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "scenarios": [(r["fixture"], r["prediction"]["status"]) for r in results]}))


if __name__ == "__main__":
    main()
