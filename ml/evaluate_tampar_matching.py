"""Evaluate the frozen correspondence gate on original TAMPAR parcel photos."""

import argparse
from collections import Counter
import hashlib
from io import BytesIO
import json
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image
import torch

from ml.assess_correspondence_gate import intervals, tally, write_new
from ml.correspondence_gate import decide, extract_evidence
from ml.inference.research_pipeline import scaled
from ml.learned_matching import fit_alignment
from ml.roma_matching import REVISION, WEIGHTS_SHA, RomaMatcher


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_photo(folder, record):
    filename = record['file']
    if Path(filename).name != filename:
        raise ValueError('Expected an image basename')
    raw = (folder / 'images' / filename).read_bytes()
    if digest(raw) != record['sha256']:
        raise ValueError(f'Image bytes changed: {filename}')
    with Image.open(BytesIO(raw)) as image:
        if list(image.size) != record['size']:
            raise ValueError(f'Image size mismatch: {filename}')
        return np.asarray(image.convert('RGB'))


def crop_photo(rgb, bbox):
    x, y, w, h = bbox
    x0, y0 = max(0, round(x + .225 * w)), max(0, round(y + .225 * h))
    x1, y1 = min(rgb.shape[1], round(x + .775 * w)), min(rgb.shape[0], round(y + .775 * h))
    if min(x1 - x0, y1 - y0) < 96:
        raise ValueError('Invalid close-up crop')
    return rgb[y0:y1, x0:x1]


def pair_features(matcher, reference, query):
    rp, qp, _ = matcher.match(reference, query)
    fit = fit_alignment(rp, qp, query.shape)
    features, _, _ = extract_evidence(reference, query, rp, qp, fit)
    return features


def check_gallery(case, records):
    query = records[case['query']]
    ref = records[case['true_reference']]
    bad = [records[filename] for filename in case['distractors']]
    if (ref['box'] != query['box'] or ref['time'] >= query['time'] or ref['zone'] != 'base'
            or case['box'] != query['box'] or case['zone'] != query['zone'] or query['zone'] == 'base'
            or len(bad) != 4 or len({record['box'] for record in bad}) != 4
            or any(record['box'] == query['box'] or record['time'] >= query['time'] or record['zone'] != 'base'
                   for record in bad)):
        raise ValueError('TAMPAR gallery identity mismatch')


def summarize(rows, policy):
    counts, _, objects = tally(rows, policy)
    ci = intervals(objects)
    # TAMPAR parcel IDs label the reference identity; this test has no pixel alignment ground truth.
    del counts['exact_and_aligned'], ci['exact_and_aligned']
    false_accepts = []
    for row in rows:
        wrong = [pair for pair in row['pairs'] if not pair['exact_source'] and decide(pair['features'], policy)['accepted']]
        if wrong:
            false_accepts.append({'query': row['query_source'], 'box': row['lineage'],
                                  'zone': row['zone'], 'markers_in_query': row['markers'],
                                  'wrong_candidates': sorted(wrong, key=lambda p: -p['features']['rank_score'])})
    return {'counts': counts, 'bootstrap_95_by_box': ci, 'wrong_only_errors': false_accepts,
            'queries_with_detectable_aruco': sum(bool(row['markers']) for row in rows),
            'queries_with_aruco_in_both_true_pair_images': sum(
                bool(row['markers'] and row['pairs'][0]['markers']) for row in rows),
            'true_pair_rejection_reasons': dict(Counter(
                reason for row in rows for reason in decide(row['pairs'][0]['features'], policy)['reason_codes']))}


def run(folder, output, cache):
    raw_manifest = (folder / 'manifest.json').read_bytes()
    manifest = json.loads(raw_manifest)
    raw_policy = Path('artifacts/evaluation/correspondence-policy-synthetic-v1.json').read_bytes()
    final_test = json.loads(Path('artifacts/evaluation/correspondence-final-test-v1.json').read_bytes())
    if digest(raw_policy) != manifest['policy_sha256'] or digest(raw_policy) != final_test['policy_sha256']:
        raise ValueError('Frozen policy changed since synthetic final test')
    policy = json.loads(raw_policy)
    records = {image['file']: image for image in manifest['images']}
    cases = manifest['queries']
    if len({row['query'] for row in cases}) != len(cases):
        raise ValueError('Duplicate TAMPAR queries')
    for case in cases:
        check_gallery(case, records)
    if output.exists() or output.with_name('pair-features.json').exists():
        raise FileExistsError('Keep the completed external evaluation intact')
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100))

    def markers(rgb):
        _, ids, _ = detector.detectMarkers(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
        return [] if ids is None else sorted(int(value) for value in ids.ravel())

    torch.set_num_threads(2)
    matcher = RomaMatcher('fast', cache)
    refs = {name: scaled(read_photo(folder, rec))[0] for name, rec in records.items() if rec['zone'] == 'base'}
    ref_markers = {name: markers(rgb) for name, rgb in refs.items()}
    started = time.monotonic()
    rows = []
    for index, case in enumerate(cases, 1):
        query = read_photo(folder, records[case['query']])
        views = {'full_photo': scaled(query)[0],
                 'close_up_crop': scaled(crop_photo(query, records[case['query']]['bbox']))[0]}
        gallery = [case['true_reference'], *case['distractors']]
        for scenario, rgb in views.items():
            pairs = [{'candidate_source': filename, 'candidate_lineage': records[filename]['box'],
                      'exact_source': candidate == 0, 'features': pair_features(matcher, refs[filename], rgb),
                      'known_source_error': None, 'markers': ref_markers[filename]}
                     for candidate, filename in enumerate(gallery)]
            rows.append({'query_source': case['query'], 'lineage': case['box'], 'scenario': scenario,
                         'zone': case['zone'], 'pairs': pairs, 'markers': markers(rgb)})
        if index % 5 == 0 or index == len(cases):
            print(f'{index}/{len(cases)} photos, {time.monotonic() - started:.1f}s', flush=True)
    features_path = output.with_name('pair-features.json')
    write_new(features_path, rows)
    report = {'status': 'TAMPAR_EXTERNAL_MATCHING_EVALUATION_COMPLETE', 'dataset': manifest['dataset'],
              'dataset_credit': manifest['source'], 'image_mirror_commit': manifest['mirror_commit'],
              'official_annotations_sha256': manifest['official_annotations_sha256'],
              'sample_manifest_sha256': digest(raw_manifest), 'policy_sha256': digest(raw_policy),
              'pair_features_sha256': digest(features_path.read_bytes()),
              'roma_revision': REVISION, 'roma_weights_sha256': WEIGHTS_SHA, 'policy': policy,
              'gpu': torch.cuda.get_device_name(), 'source_boxes': len({case['box'] for case in cases}),
              'earlier_reference_photos': len(refs), 'later_query_photos': len(cases),
              'selection': manifest['selection'], 'close_up': manifest['close_up'],
              'scoring': 'One true earlier picture plus three of four distractors, success requires all four mixes. Wrong-only gallery has all four distractors. Ground truth is physical box identity, not a pixel-level alignment label.',
              'geometry_input': 'Aspect-ratio-preserving application resize, full original photographs or the declared crop. No ground-truth keypoints or mask passed to the matcher.',
              'conditions': {key: summarize([r for r in rows if r['scenario'] == key], policy)
                             for key in ('full_photo', 'close_up_crop')},
              'promotion': 'HOLD',
              'limits': ['TAMPAR is a staged parcel tampering dataset, not an in-use courier pilot or a dent benchmark.',
                         'The close-ups are crops of real photos, not independently captured close-ups.',
                         'One earlier reference and four wrong candidates per query. Pose-based sampling and visual identifiers can affect results.']}
    write_new(output, report)
    print(json.dumps({key: value['counts'] for key, value in report['conditions'].items()}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', default='/models/roma')
    options = parser.parse_args()
    run(options.data, options.output, options.cache)
