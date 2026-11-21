"""Local runtime measurements on procedural fixtures, not accuracy claims."""
import json
import platform
import resource
import time
from pathlib import Path

import numpy as np
from PIL import Image

from ml.features.dino import DinoEncoder
from ml.inference.ranker import ShadowAdvisor
from services.worker.baseline import analyze_images


def main():
    folder=Path("tests/fixtures/images")
    query=Image.open(folder/"changed.png").convert("RGB")
    started=time.monotonic()
    advisor=ShadowAdvisor("artifacts/models/reference-ranker",DinoEncoder(cache_dir=Path(".local/dino-cache")))
    cold=time.monotonic()-started
    measurements=[]
    for count in (1,6,12,40):
        rows=[]
        for repeat in range(3):
            def references():
                for i in range(count):
                    yield str(i),Image.open(folder/"dispatch.png").convert("RGB")
            start=time.monotonic()
            analyze_images("query",query,references())
            baseline=time.monotonic()-start
            advisor.compare(query,references())
            total=time.monotonic()-start
            rows.append({"baseline_seconds":baseline,"baseline_plus_shadow_seconds":total})
        measurements.append({"reference_count":count,"runs":rows,
            "baseline_p95":float(np.percentile([r["baseline_seconds"] for r in rows],95)),
            "with_shadow_p95":float(np.percentile([r["baseline_plus_shadow_seconds"] for r in rows],95))})
        print(json.dumps(measurements[-1]),flush=True)
    report={"scope":"LOCAL_PROCEDURAL_FIXTURES_LATENCY_ONLY","python":platform.python_version(),
        "cold_model_load_seconds":cold,"max_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "measurements":measurements,"limitations":["Three repeats per size; not a load-test confidence interval.",
        "Repeated small fixtures; not representative of diverse full-resolution customer photos.","Excludes network upload, job queue and wallet time."]}
    Path("artifacts/evaluation/vision-latency.json").write_text(json.dumps(report,indent=2),encoding="utf-8")


if __name__ == "__main__":main()
