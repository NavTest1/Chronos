"""Public raw edge-time -> validated Chronos processed arrays builder.

Does not attempt to re-create missing historical OSM/LTA collection scripts.
When a GraphML file is supplied, its OSM node IDs and lon/lat coordinates are
joined exactly and projected to EPSG:3414 before freezing the dataset.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import uuid
from pathlib import Path

from chronos.preprocess.generate_micro_raw import generate_micro_raw
from chronos.preprocess.processor import preprocess_edge_time
from chronos.init.coordinates import augment_coordinates_from_graphml
from chronos.init.validate_dataset import validate_processed_dataset


def build(raw: Path, output: Path, graphml: Path | None = None, *,
          coordinate_crs: str | None = None, expected_nodes: int | None = None,
          expected_edges: int | None = None, expected_buckets: int | None = None) -> dict:
    """Fail-closed build into a temporary directory, publish only after validation."""
    raw = Path(raw).resolve()
    output = Path(output).resolve()
    if not raw.is_file():
        raise FileNotFoundError(f"Raw edge-time file not found: {raw}")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output}")
    if graphml is not None and coordinate_crs is not None:
        raise ValueError("GraphML-based augmentation and inline coordinate CRS are alternatives")
    if graphml is not None and not Path(graphml).is_file():
        raise FileNotFoundError(f"GraphML not found: {graphml}")
    output.parent.mkdir(parents=True, exist_ok=True)
    work = output.parent / ('.' + output.name + '.build-' + uuid.uuid4().hex)
    try:
        preprocess_edge_time(raw, work, dataset_name='Chronos-SG-2026',
                             time_granularity_minutes=15, coordinate_crs=coordinate_crs)
        if graphml is not None:
            summary = augment_coordinates_from_graphml(work, Path(graphml).resolve(),
                target_crs='EPSG:3414', write=True)
            if summary.get('coverage_ratio') != 1.0:
                raise RuntimeError('Full projected-coordinate coverage not achieved')
        summary = validate_processed_dataset(work)
        checks = {'num_nodes': expected_nodes, 'num_edges': expected_edges,
                  'num_buckets': expected_buckets}
        for field, desired in checks.items():
            if desired is not None and summary[field] != desired:
                raise ValueError(f'Unexpected {field}: {summary[field]}, expected {desired}')
        os.replace(work, output)
        return {'status': 'PASS', 'processed_dir': str(output),
                'num_nodes': summary['num_nodes'], 'num_edges': summary['num_edges'],
                'num_buckets': summary['num_buckets'],
                'dataset_fingerprint': summary['dataset_fingerprint']}
    except Exception:
        if work.exists():
            shutil.rmtree(work)
        raise


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    cmd = ap.add_subparsers(dest='cmd', required=True)
    b = cmd.add_parser('from-edge-time', help='Build from documented raw edge/time table')
    b.add_argument('--input', required=True, type=Path)
    b.add_argument('--output', required=True, type=Path)
    b.add_argument('--graphml', type=Path, help='Matching OSM road graph (with node lon/lat)')
    b.add_argument('--coordinate-crs', help='Required for projected inline coordinates, e.g. EPSG:3414')
    b.add_argument('--expect-nodes', type=int)
    b.add_argument('--expect-edges', type=int)
    b.add_argument('--expect-buckets', type=int)
    v = cmd.add_parser('validate', help='Validate an existing processed dataset')
    v.add_argument('--processed', required=True, type=Path)
    m = cmd.add_parser('micro', help='Build portable micro fixture, not paper data')
    m.add_argument('--run-dir', default='runs/micro-data', type=Path)
    args = ap.parse_args(argv)
    if args.cmd == 'micro':
        run = args.run_dir.resolve()
        run.mkdir(parents=True, exist_ok=True)
        raw = run / 'micro_edge_time.csv'
        generated = generate_micro_raw(raw)
        result = build(raw, run/'processed',coordinate_crs=generated['coordinate_crs'],
                       expected_nodes=4,expected_edges=6,expected_buckets=4)
        result['synthetic_fixture_not_paper_data'] = True
    elif args.cmd == 'validate':
        result = validate_processed_dataset(args.processed.resolve())
    else:
        result = build(args.input, args.output, args.graphml,
                       coordinate_crs=args.coordinate_crs,
                       expected_nodes=args.expect_nodes, expected_edges=args.expect_edges,
                       expected_buckets=args.expect_buckets)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
