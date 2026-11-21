"""Freeze and assess a synthetic-only correspondence operating point."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
from itertools import combinations, product
import json
from pathlib import Path

import numpy as np

from ml.correspondence_gate import POLICY, decide


ARTIFACTS = Path('artifacts/kaggle')
REFERENCES = {
    'selection': 'solishu/notebook-unihack26-correspondence-selection',
    'calibration': 'shusolitude/notebook-unihack26-correspondence-calibration',
}
SCENARIOS = ('planar', 'close_up', 'blur', 'shadow', 'occlusion', 'added_mark')
ALIGNMENT_P95_PX = 3
TARGETS = {'max_wrong_gallery_rate': .02, 'max_mixed_wrong_rate': .02,
           'min_positive_coverage': .80, 'min_exact_source_rate': .95}
GRID = {'min_median_ncc': [.55, .60, .65, .70],
        'min_agreeing_fraction': [.45, .50, .55, .60, .65]}


def digest(data):
    return sha256(data).hexdigest()


def write_new(path, data):
    with path.open('x', encoding='utf-8', newline='\n') as output:
        json.dump(data, output, indent=2, allow_nan=False)
        output.write('\n')


def load_run(role):
    folder = ARTIFACTS / f'correspondence-{role}-v1'
    receipt = json.loads(folder.with_name(folder.name + '-outputs.json').read_bytes())
    if (receipt['reference'] != REFERENCES[role] or receipt['version'] != 1
            or receipt['run_status'] != 'COMPLETE' or receipt['collection_status'] != 'COMPLETE'):
        raise ValueError(f'{role}: Kaggle run identity or completion mismatch')
    entries = {entry['name']: entry for entry in receipt['files']}
    names = ('gate/report.json', 'gate/features.json', 'source-hashes.json')
    payloads = []
    for name in names:
        raw = (folder / name).read_bytes()
        if len(raw) != entries[name]['bytes'] or digest(raw) != entries[name]['sha256']:
            raise ValueError(f'{role}: artifact hash mismatch: {name}')
        payloads.append(json.loads(raw))
    report, rows, sources = payloads
    if (report['role'] != role or report['status'] != 'FEATURE_EXTRACTION_COMPLETE'
            or report['test'] != 'NOT_READ' or report['diagnostic_policy'] != POLICY
            or report['source_sha256'] != sources['ml/evaluate_correspondence_gate.py']
            or report['gate_source_sha256'] != sources['ml/correspondence_gate.py']
            or sources['ml/correspondence_gate.py'] != digest(Path('ml/correspondence_gate.py').read_bytes())
            or len(rows) != report['completed_queries'] or len(rows) != report['target_queries']):
        raise ValueError(f'{role}: invalid feature protocol or incomplete run')

    lineages = {row['lineage'] for row in rows}
    queries = {(row['lineage'], row['query_source']) for row in rows}
    keys = {(row['lineage'], row['query_source'], row['scenario']) for row in rows}
    if (len(lineages) != report['source_objects'] or len(queries) != report['source_query_images']
            or len(keys) != len(rows) or keys != {(*query, s) for query in queries for s in SCENARIOS}):
        raise ValueError(f'{role}: invalid query grid')
    for row in rows:
        pairs = row['pairs']
        exact = [p for p in pairs if p['exact_source']]
        wrong = [p for p in pairs if not p['exact_source']]
        if (len(exact) != 1 or exact[0]['candidate_source'] != row['query_source']
                or exact[0]['candidate_lineage'] != row['lineage'] or len(wrong) != 4
                or len({p['candidate_lineage'] for p in wrong}) != 4
                or any(p['candidate_lineage'] not in lineages - {row['lineage']} for p in wrong)
                or any(decide(p['features']) != p['decision'] for p in pairs)):
            raise ValueError(f'{role}: invalid candidate identity or diagnostic decision')
    provenance = {'reference': receipt['reference'], 'version': receipt['version'],
                  'sha256': {name: entries[name]['sha256'] for name in names},
                  'manifest_sha256': report['manifest_sha256'], 'sources': sources,
                  'roma_revision': report['roma_revision'], 'roma_weights_sha256': report['roma_weights_sha256'],
                  'source_objects': len(lineages), 'source_images': len(queries),
                  'queries': len(rows), 'quarantined': report['quarantined_calibration_objects']}
    return rows, lineages, provenance


def outcomes(row, policy):
    pairs = row['pairs']
    # Equal accepted rank scores are counted against the true source in this evaluation.
    ranked = sorted((i for i, pair in enumerate(pairs) if decide(pair['features'], policy)['accepted']),
                    key=lambda i: (-pairs[i]['features']['rank_score'], pairs[i]['exact_source']))
    wrong = [i for i, pair in enumerate(pairs) if not pair['exact_source']]
    winners = [next((i for i in ranked if i != omitted), None) for omitted in wrong]
    exact = all(i is not None and pairs[i]['exact_source'] for i in winners)
    error = next(pair['known_source_error'] for pair in pairs if pair['exact_source'])
    return {'queries': 1, 'positive_accepted': all(i is not None for i in winners),
            'exact_source_selected': exact, 'exact_and_aligned': exact and error is not None
            and error['p95_px'] <= ALIGNMENT_P95_PX,
            'mixed_wrong_selected': any(i in wrong for i in winners),
            'wrong_gallery_accepted': any(i in wrong for i in ranked)}


def tally(rows, policy):
    overall = Counter()
    scenarios, lineages = defaultdict(Counter), defaultdict(Counter)
    for row in rows:
        result = outcomes(row, policy)
        for counts in (overall, scenarios[row['scenario']], lineages[row['lineage']]):
            for key, value in result.items():
                counts[key] += value
    return dict(overall), dict(scenarios), lineages


def freeze(path):
    rows, lineages, source = load_run('selection')
    baseline, scenarios, _ = tally(rows, POLICY)
    protocol = {'selection': source, 'selection_lineages': sorted(lineages),
                'selection_diagnostic_counts': baseline, 'selection_by_scenario': scenarios,
                'assessor_sha256': digest(Path(__file__).read_bytes()), 'diagnostic_policy': POLICY,
                'grid': GRID, 'targets': TARGETS, 'alignment_p95_px': ALIGNMENT_P95_PX,
                'ranking': 'Filter accepted pairs, then descending rank_score. Ties favor wrong candidates for scoring.',
                'galleries': 'Use all four true-source plus three-distractor combinations. Count a query as correct or covered only if all four qualify. Count a mixed error if any chooses a wrong source. Wrong-only uses all four distractors.',
                'selection_rule': 'Require both wrong rates <=2% and positive coverage >=80%. Prefer the pool with exact-source success >=95% if any. Minimize combined error counts, maximize exact+aligned then exact returns, then favor higher NCC and agreement. With no eligible candidate, select none.',
                'test': 'NOT_READ', 'scope': 'SYNTHETIC_ONLY'}
    write_new(path, protocol)
    print(json.dumps(baseline))


def intervals(lineages):
    metrics = ('positive_accepted', 'exact_source_selected', 'exact_and_aligned',
               'mixed_wrong_selected', 'wrong_gallery_accepted')
    counts = np.array([[item[key] for key in ('queries', *metrics)] for item in lineages.values()])
    sampled = np.random.default_rng(26).integers(0, len(counts), size=(4000, len(counts)))
    totals = counts[sampled].sum(axis=1)
    bounds = np.quantile(totals[:, 1:] / totals[:, :1], [.025, .975], axis=0).T
    return {key: ci.tolist() for key, ci in zip(metrics, bounds, strict=True)}


def assess(protocol_path, report_path, policy_path):
    raw_protocol = protocol_path.read_bytes()
    protocol = json.loads(raw_protocol)
    if protocol['assessor_sha256'] != digest(Path(__file__).read_bytes()):
        raise ValueError('The assessor changed after the protocol was frozen')
    if report_path.exists() or policy_path.exists():
        raise FileExistsError('Use new output paths to preserve existing results')
    rows, lineages, source = load_run('calibration')
    selection = protocol['selection']
    shared = ('manifest_sha256', 'sources', 'roma_revision', 'roma_weights_sha256')
    if (lineages.intersection(protocol['selection_lineages'])
            or any(source[key] != selection[key] for key in shared)):
        raise ValueError('Selection/calibration source overlap or protocol mismatch')

    grid, targets = protocol['grid'], protocol['targets']
    candidates = []
    for ncc, agreement in product(grid['min_median_ncc'], grid['min_agreeing_fraction']):
        policy = {**protocol['diagnostic_policy'], 'min_median_ncc': ncc, 'min_agreeing_fraction': agreement}
        counts, _, _ = tally(rows, policy)
        n = counts['queries']
        candidates.append({'min_median_ncc': ncc, 'min_agreeing_fraction': agreement, **counts,
                           'eligible': counts['wrong_gallery_accepted'] / n <= targets['max_wrong_gallery_rate']
                           and counts['mixed_wrong_selected'] / n <= targets['max_mixed_wrong_rate']
                           and counts['positive_accepted'] / n >= targets['min_positive_coverage'],
                           'meets_exact_source_target': counts['exact_source_selected'] / n >= targets['min_exact_source_rate']})
    eligible = [item for item in candidates if item['eligible']]
    finalists = [item for item in eligible if item['meets_exact_source_target']] or eligible
    selected = min(finalists, default=None, key=lambda item: (
        item['wrong_gallery_accepted'] + item['mixed_wrong_selected'], -item['exact_and_aligned'],
        -item['exact_source_selected'], -item['min_median_ncc'], -item['min_agreeing_fraction']))
    baseline, _, _ = tally(rows, protocol['diagnostic_policy'])
    report = {'status': 'SYNTHETIC_OPERATING_POINT_SELECTED' if selected else 'NO_ELIGIBLE_OPERATING_POINT',
              'protocol_sha256': digest(raw_protocol), 'calibration': source, 'diagnostic_counts': baseline,
              'grid': candidates, 'selected': selected, 'test': 'NOT_READ', 'promotion': 'HOLD',
              'limits': ['Queries reuse source objects and are not independent physical parcels.',
                         'Threshold search and assessment use the same calibration split.',
                         'The benchmark does not label wrong-face, real viewpoint or insufficient-evidence cases.']}
    if selected:
        policy = {**protocol['diagnostic_policy'], **{key: selected[key] for key in grid},
                  'calibration': 'SYNTHETIC_CALIBRATION_V1'}
        _, scenarios, groups = tally(rows, policy)
        report['selected_policy_sha256'] = digest((json.dumps(policy, indent=2, allow_nan=False) + '\n').encode())
        report['selected_by_scenario'] = scenarios
        report['bootstrap_95_by_source_object'] = intervals(groups)
        if not selected['wrong_gallery_accepted']:
            report['zero_event_95_upper_bound_by_source_object'] = 1 - .05 ** (1 / len(groups))
        write_new(policy_path, policy)
    write_new(report_path, report)
    print(json.dumps({'status': report['status'], 'selected': selected, 'baseline': baseline}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('freeze', 'assess'))
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--policy', type=Path)
    args = parser.parse_args()
    if args.phase == 'freeze':
        freeze(args.protocol)
    else:
        if args.report is None or args.policy is None:
            parser.error('assess requires --report and --policy')
        assess(args.protocol, args.report, args.policy)


if __name__ == '__main__':
    main()
