"""Collect appearance-verification features on controlled positive/negative pairs."""
import json
import time
from pathlib import Path

import cv2
import numpy as np
import torch

from ml.correspondence_gate import POLICY, extract_evidence, decide
from ml.data.parcel3d_audit import find_annotations
from ml.data.parcel_pairs_v2 import ParcelPairs
from ml.evaluate_matching_stress import gallery_lineages, query_variant, visible_transfer_error
from ml.evaluate_roma_negatives import wrong_object_gallery
from ml.learned_matching import fit_alignment
from ml.roma_matching import RomaMatcher, REVISION, WEIGHTS_SHA, sha256_file

QUARANTINED_CALIBRATION_OBJECTS = (
    'google_scanned_objects/Perricone_MD_Hypoallergenic_Gentle_Cleanser',
    'google_scanned_objects/SAMe_200',
)
SCENARIOS = ('planar', 'close_up', 'blur', 'shadow', 'occlusion', 'added_mark')


def run(input_root, manifest, output, role='selection', max_seconds=10800):
    if role not in ('selection', 'calibration'):
        raise ValueError('FINAL_TEST_NOT_AUTHORIZED_BY_THIS_PROTOCOL')
    torch.set_num_threads(2)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    data = ParcelPairs(find_annotations(Path(input_root))['train'].parent, manifest, role, size=518)
    grouped, excluded = gallery_lineages(data.rows)
    quarantined = {k: len(v) for k, v in grouped.items() if role == 'calibration' and k in QUARANTINED_CALIBRATION_OBJECTS}
    grouped = {k: v for k, v in grouped.items() if k not in quarantined}
    indices = [i for key in sorted(grouped) for i in grouped[key][:4]]
    images, paths, lineages = [], [], []
    for i in indices:
        sample = data[i]
        images.append((sample['reference'].numpy().transpose(1, 2, 0)*255).round().astype(np.uint8))
        paths.append(sample['source'])
        lineages.append(sample['lineage'])
    queries = [i for key in sorted(grouped) for i in [j for j, value in enumerate(lineages) if value == key][:2]]
    matcher = RomaMatcher('fast')
    rows, started = [], time.monotonic()

    def save(status):
        report = {'status': status, 'role': role, 'feature_version': POLICY['version'], 'diagnostic_policy': POLICY,
                  'manifest_sha256': data.manifest_sha256, 'source_sha256': sha256_file(__file__),
                  'gate_source_sha256': sha256_file(Path(__file__).parent / 'correspondence_gate.py'),
                  'roma_revision': REVISION, 'roma_weights_sha256': WEIGHTS_SHA,
                  'completed_queries': len(rows), 'target_queries': len(queries)*len(SCENARIOS),
                  'source_objects': len(grouped), 'source_query_images': len(queries),
                  'quarantined_calibration_objects': quarantined, 'excluded_small_lineages': excluded,
                  'seconds': time.monotonic()-started, 'test': 'NOT_READ', 'promotion': 'HOLD',
                  'calibration': 'FEATURES_ONLY_NO_THRESHOLD_CHOSEN',
                  'limits': ['Synthetic planar queries and appearance marks; not real viewpoint/physical identity labels.',
                             'Provided foreground crops; geometric support uses inferred correspondence, no truth masks.',
                             'Positive gallery has one known source and three other objects; negative gallery has four other objects.',
                             'Two calibration lineages quarantined conservatively due to unresolved near-pair screen; original split unchanged.',
                             'No threshold selection in this notebook, and no production promotion.']}
        for name, value in (('features.json', rows), ('report.json', report)):
            tmp = output / (name+'.tmp')
            tmp.write_text(json.dumps(value, indent=2), encoding='utf-8')
            tmp.replace(output / name)
        print(json.dumps({'status': status, 'completed': len(rows), 'seconds': report['seconds']}), flush=True)
        return report

    for index in queries:
        candidates = [index, *wrong_object_gallery(lineages, index)]
        for scenario in SCENARIOS:
            if time.monotonic()-started > max_seconds:
                return save('PARTIAL_REQUIRES_REVIEW')
            query, truth, visible = query_variant(images[index], 'planar' if scenario == 'added_mark' else scenario, index)
            if scenario == 'added_mark':
                cv2.line(query, (210, 220), (290, 270), (25, 30, 35), 5)
            pairs = []
            for candidate in candidates:
                before = time.perf_counter()
                rp, qp, stats = matcher.match(images[candidate], query)
                fit = fit_alignment(rp, qp, query.shape)
                features, _, _ = extract_evidence(images[candidate], query, rp, qp, fit)
                pairs.append({'candidate_source': paths[candidate], 'candidate_lineage': lineages[candidate],
                              'exact_source': candidate == index, 'features': features, 'decision': decide(features),
                              'known_source_error': visible_transfer_error(fit['homography'], truth, visible) if candidate == index else None,
                              'seconds': time.perf_counter()-before})
            rows.append({'query_source': paths[index], 'lineage': lineages[index], 'scenario': scenario, 'pairs': pairs})
        save('RUNNING')
    return save('FEATURE_EXTRACTION_COMPLETE')
