"""Validate frozen processed Chronos-SG arrays before OfflineIndex v3 Init."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import numpy as np

from ..common.config import PATHS
from ..common.dataset import load_dataset


def validate_processed_dataset(processed_dir: Path) -> dict:
    """Validate the frozen ``G=(V,E,B,T)`` arrays consumed by Chronos Init."""

    dataset = load_dataset(processed_dir, mmap_mode=None)
    processed_dir = Path(processed_dir)
    manifest = dict(dataset.manifest)
    files = dict(manifest["files"])

    if dataset.speed.shape != dataset.travel_time.shape:
        raise ValueError("speed and travel_time shapes do not match.")
    if dataset.travel_time.ndim != 2:
        raise ValueError("travel_time must have shape [B, |E|].")
    if dataset.edge_index.shape != (2, dataset.num_edges):
        raise ValueError("edge_index must have shape [2, num_edges].")
    if dataset.edge_index_node_ids.shape != dataset.edge_index.shape:
        raise ValueError("edge_index_node_ids must have the same shape as edge_index.")
    if len(dataset.edge_metadata) != dataset.num_edges:
        raise ValueError("edge_metadata row count does not match num_edges.")
    if int(dataset.manifest.get("num_time_buckets", dataset.num_time_buckets)) != dataset.num_time_buckets:
        raise ValueError("manifest num_time_buckets does not match arrays.")
    if int(dataset.manifest.get("num_edges", dataset.num_edges)) != dataset.num_edges:
        raise ValueError("manifest num_edges does not match arrays.")
    if int(dataset.manifest.get("num_nodes", dataset.num_nodes)) != dataset.num_nodes:
        raise ValueError("manifest num_nodes does not match arrays.")

    if np.any(dataset.edge_index < 0) or np.any(dataset.edge_index >= dataset.num_nodes):
        raise ValueError("edge_index contains out-of-range node positions.")
    expected_node_ids = dataset.node_ids[dataset.edge_index]
    if not np.array_equal(dataset.edge_index_node_ids, expected_node_ids):
        raise ValueError("edge_index_node_ids is not exactly node_ids[edge_index].")

    for name, values in (("travel_time", dataset.travel_time), ("speed", dataset.speed)):
        if np.any(~np.isfinite(values)):
            raise ValueError(f"{name} contains NaN or Inf.")
        if np.any(values <= 0):
            raise ValueError(f"{name} values must all be positive.")

    if "edge_idx" in dataset.edge_metadata.columns:
        expected = np.arange(dataset.num_edges)
        actual = dataset.edge_metadata["edge_idx"].to_numpy(dtype=np.int64)
        if not np.array_equal(actual, expected):
            raise ValueError("edge_metadata.edge_idx is not strictly aligned with array columns.")
    length_col = next((name for name in ("osm_length_m", "length_m", "edge_length_m") if name in dataset.edge_metadata.columns), None)
    if length_col and np.any(dataset.edge_metadata[length_col].to_numpy(dtype=float) <= 0):
        raise ValueError("edge length values must be positive.")

    time_buckets = dataset.time_buckets.copy()
    if "time_idx" in time_buckets.columns:
        if not np.array_equal(time_buckets["time_idx"].to_numpy(dtype=np.int64), np.arange(dataset.num_time_buckets)):
            raise ValueError("time_idx must run continuously from 0 to B-1.")
    if "time_bucket" in time_buckets.columns:
        times = time_buckets["time_bucket"]
        if times.duplicated().any():
            raise ValueError("time_bucket contains duplicates.")
        deltas = times.diff().dropna()
        if len(deltas) and not (deltas == deltas.iloc[0]).all():
            raise ValueError("time buckets are not evenly spaced.")
        if len(deltas) and deltas.iloc[0].total_seconds() != 15 * 60:
            raise ValueError("time buckets are not spaced at 15 minutes.")

    coordinate_summary = _validate_coordinate_sidecar(dataset, processed_dir, files, manifest)

    edge_pairs = [tuple(map(int, dataset.edge_index[:, idx])) for idx in range(dataset.num_edges)]
    pair_counts = Counter(edge_pairs)
    self_loop_count = sum(1 for source, target in edge_pairs if source == target)
    multi_edge_pair_count = sum(1 for count in pair_counts.values() if count > 1)
    file_hashes = {
        name: _sha256(processed_dir / rel)
        for name, rel in sorted(files.items())
        if (processed_dir / rel).exists()
    }
    summary = {
        "num_nodes": dataset.num_nodes,
        "num_edges": dataset.num_edges,
        "num_buckets": dataset.num_time_buckets,
        "finite_travel_time_ratio": float(np.mean(np.isfinite(dataset.travel_time))),
        "directed": True,
        "self_loop_count": self_loop_count,
        "multi_edge_pair_count": multi_edge_pair_count,
        **coordinate_summary,
        "file_sha256": file_hashes,
        "dataset_fingerprint": hashlib.sha256(json.dumps(file_hashes, sort_keys=True).encode("utf-8")).hexdigest(),
    }
    print(json.dumps(summary, indent=2))
    return summary


def _validate_coordinate_sidecar(dataset, processed_dir: Path, files: dict, manifest: dict) -> dict:
    coord_file = files.get("node_coordinates") or manifest.get("node_coordinates")
    if not coord_file:
        return {
            "coordinate_valid": False,
            "coordinate_coverage_ratio": 0.0,
            "coordinate_sha256": None,
            "coordinate_crs": None,
            "shortest_path_backend": "Dijkstra",
            "heuristic_type": "zero",
            "v_max_mps": None,
        }
    coords = np.asarray(dataset.node_coordinates, dtype=float)
    if coords.shape != (dataset.num_nodes, 2):
        raise ValueError("sg_node_coordinates.npy must have shape (num_nodes, 2).")
    if not np.all(np.isfinite(coords)):
        raise ValueError("sg_node_coordinates.npy contains NaN or Inf; no silent coordinate fill is allowed.")
    return {
        "coordinate_valid": True,
        "coordinate_coverage_ratio": 1.0,
        "coordinate_sha256": _sha256(processed_dir / coord_file),
        "coordinate_crs": manifest.get("coordinate_crs") or manifest.get("coordinate_system"),
        "shortest_path_backend": "A*_with_coordinates",
        "heuristic_type": "euclidean_over_v_max",
        "v_max_mps": _global_v_max_mps(dataset),
    }


def _global_v_max_mps(dataset) -> float:
    length_col = next((name for name in ("osm_length_m", "length_m", "edge_length_m") if name in dataset.edge_metadata.columns), None)
    lengths = np.ones(dataset.num_edges, dtype=float) if length_col is None else dataset.edge_metadata[length_col].to_numpy(dtype=float)
    travel_time = np.asarray(dataset.travel_time, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        speed = lengths[None, :] / travel_time
    finite = speed[np.isfinite(speed) & (speed > 0.0)]
    safety_factor = max(1.0, float(dataset.manifest.get("astar_speed_safety_factor", 1.05)))
    return (float(np.max(finite)) if finite.size else 1.0) * safety_factor


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description="Validate Chronos processed graph arrays.")
    parser.add_argument("--processed-dir", type=Path, default=PATHS.processed_dir)
    return validate_processed_dataset(parser.parse_args(argv).processed_dir)


if __name__ == "__main__":
    main()
