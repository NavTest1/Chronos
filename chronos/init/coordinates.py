"""Explicit coordinate-sidecar augmentation for Chronos-SG processed data."""

import argparse
import hashlib
import io
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Tuple

import numpy as np

from ..common.config import PATHS
from ..common.dataset import load_dataset


DEFAULT_SINGAPORE_CRS = "EPSG:3414"


class CoordinateAugmentationError(ValueError):
    """Raised when GraphML coordinates cannot safely augment the dataset."""


def augment_coordinates_from_graphml(
    processed_dir: Path,
    graphml_path: Path,
    target_crs: str = DEFAULT_SINGAPORE_CRS,
    write: bool = False,
    safety_factor: float = 1.05,
) -> dict:
    """Match OSM node ids exactly and optionally write ``sg_node_coordinates.npy``."""

    processed_dir = Path(processed_dir)
    graphml_path = Path(graphml_path)
    dataset = load_dataset(processed_dir, mmap_mode=None)
    lon_lat = _read_graphml_lon_lat(graphml_path)
    node_ids = np.asarray(dataset.node_ids)
    matched = []
    missing = []
    for node_id in node_ids:
        key = str(int(node_id))
        if key in lon_lat:
            matched.append(lon_lat[key])
        else:
            missing.append(int(node_id))
    coverage = len(matched) / max(1, len(node_ids))
    if missing:
        summary = _summary_without_coordinates(dataset, processed_dir, graphml_path, target_crs, coverage, missing)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return summary

    coords = _project_lon_lat(np.asarray(matched, dtype=np.float64), target_crs)
    if coords.shape != (dataset.num_nodes, 2) or not np.all(np.isfinite(coords)):
        raise CoordinateAugmentationError("Projected coordinates must be finite with shape (num_nodes, 2).")
    coord_sha = _npy_sha256(coords)
    manifest = _updated_manifest(dataset, graphml_path, target_crs, coord_sha, safety_factor)
    fingerprint = _dataset_fingerprint(processed_dir, manifest, extra_files={"sg_node_coordinates.npy": _npy_bytes(coords)})
    summary = {
        "processed_dir": str(processed_dir),
        "graphml_path": str(graphml_path),
        "node_count": int(dataset.num_nodes),
        "matched_count": int(dataset.num_nodes),
        "missing_count": 0,
        "coverage_ratio": 1.0,
        "coordinate_crs": target_crs,
        "coordinate_units": "meters",
        "bounds": {
            "min_x": float(np.min(coords[:, 0])),
            "max_x": float(np.max(coords[:, 0])),
            "min_y": float(np.min(coords[:, 1])),
            "max_y": float(np.max(coords[:, 1])),
        },
        "coordinate_sha256": coord_sha,
        "dataset_fingerprint": fingerprint,
        "shortest_path_backend": "A*_with_coordinates",
        "heuristic_type": "euclidean_over_v_max",
        "v_max_mps": _global_v_max_mps(dataset, safety_factor),
        "write": bool(write),
    }
    if write:
        _atomic_save_npy(processed_dir / "sg_node_coordinates.npy", coords)
        manifest["fingerprint"] = fingerprint
        _atomic_write_text(processed_dir / "dataset_manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def _read_graphml_lon_lat(path: Path) -> Dict[str, Tuple[float, float]]:
    if not Path(path).exists():
        raise CoordinateAugmentationError(f"GraphML file does not exist: {path}")
    root = ET.parse(path).getroot()
    key_names = {}
    for key in root.iter():
        if _local_name(key.tag) != "key":
            continue
        if key.attrib.get("for") != "node":
            continue
        name = key.attrib.get("attr.name") or key.attrib.get("id")
        key_names[key.attrib["id"]] = str(name).lower()
    x_keys = {key for key, name in key_names.items() if name in {"x", "lon", "longitude"}}
    y_keys = {key for key, name in key_names.items() if name in {"y", "lat", "latitude"}}
    if not x_keys or not y_keys:
        raise CoordinateAugmentationError("GraphML must contain node x/y or lon/lat data keys.")
    result: Dict[str, Tuple[float, float]] = {}
    for node in root.iter():
        if _local_name(node.tag) != "node":
            continue
        values = {}
        for child in node:
            if _local_name(child.tag) == "data":
                values[child.attrib.get("key")] = child.text
        x_value = next((values[key] for key in x_keys if key in values), None)
        y_value = next((values[key] for key in y_keys if key in values), None)
        if x_value is None or y_value is None:
            continue
        x = float(x_value)
        y = float(y_value)
        if np.isfinite(x) and np.isfinite(y):
            result[str(node.attrib["id"])] = (x, y)
    return result


def _project_lon_lat(lon_lat: np.ndarray, target_crs: str) -> np.ndarray:
    try:
        from pyproj import Transformer
    except ImportError as exc:
        raise CoordinateAugmentationError("pyproj is required to project GraphML lon/lat coordinates.") from exc
    transformer = Transformer.from_crs("EPSG:4326", str(target_crs), always_xy=True)
    xs, ys = transformer.transform(lon_lat[:, 0], lon_lat[:, 1])
    return np.column_stack([xs, ys]).astype(np.float64)


def _updated_manifest(dataset, graphml_path: Path, target_crs: str, coord_sha: str, safety_factor: float) -> dict:
    manifest = dict(dataset.manifest)
    files = dict(manifest["files"])
    files["node_coordinates"] = "sg_node_coordinates.npy"
    manifest.update(
        {
            "files": files,
            "coordinate_source": str(graphml_path),
            "coordinate_source_type": "graphml_osm_node_x_y",
            "coordinate_crs": str(target_crs),
            "coordinate_units": "meters",
            "coordinate_node_id_match_ratio": 1.0,
            "coordinate_sha256": coord_sha,
            "shortest_path_backend": "A*_with_coordinates",
            "heuristic_type": "euclidean_over_v_max",
            "astar_speed_safety_factor": max(1.0, float(safety_factor)),
            "v_max_mps": _global_v_max_mps(dataset, safety_factor),
        }
    )
    return manifest


def _summary_without_coordinates(dataset, processed_dir: Path, graphml_path: Path, target_crs: str, coverage: float, missing: list) -> dict:
    return {
        "processed_dir": str(processed_dir),
        "graphml_path": str(graphml_path),
        "node_count": int(dataset.num_nodes),
        "matched_count": int(dataset.num_nodes - len(missing)),
        "missing_count": int(len(missing)),
        "coverage_ratio": float(coverage),
        "missing_node_ids_sample": missing[:20],
        "coordinate_crs": target_crs,
        "coordinate_sha256": None,
        "dataset_fingerprint": _dataset_fingerprint(processed_dir, dataset.manifest),
        "shortest_path_backend": "Dijkstra",
        "heuristic_type": "zero",
        "write": False,
    }


def _dataset_fingerprint(processed_dir: Path, manifest: dict, extra_files: dict = None) -> str:
    digest = hashlib.sha256(json.dumps({k: v for k, v in manifest.items() if k != "fingerprint"}, sort_keys=True, default=str).encode("utf-8"))
    for rel in sorted(dict(manifest.get("files", {})).values()):
        path = Path(processed_dir) / rel
        if path.name == "sg_node_coordinates.npy" and extra_files and path.name in extra_files:
            digest.update(extra_files[path.name])
        elif path.exists():
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
    return digest.hexdigest()


def _global_v_max_mps(dataset, safety_factor: float) -> float:
    length_col = next((name for name in ("osm_length_m", "length_m", "edge_length_m") if name in dataset.edge_metadata.columns), None)
    lengths = np.ones(dataset.num_edges, dtype=float) if length_col is None else dataset.edge_metadata[length_col].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        speeds = lengths[None, :] / np.asarray(dataset.travel_time, dtype=float)
    finite = speeds[np.isfinite(speeds) & (speeds > 0.0)]
    return (float(np.max(finite)) if finite.size else 1.0) * max(1.0, float(safety_factor))


def _npy_bytes(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, array)
    return buffer.getvalue()


def _npy_sha256(array: np.ndarray) -> str:
    return hashlib.sha256(_npy_bytes(array)).hexdigest()


def _atomic_save_npy(path: Path, array: np.ndarray) -> None:
    tmp = Path(path).with_suffix(Path(path).suffix + ".tmp")
    with tmp.open("wb") as handle:
        np.save(handle, array)
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = Path(path).with_suffix(Path(path).suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description="Dry-run or write sg_node_coordinates.npy from an OSM GraphML file.")
    parser.add_argument("--processed-dir", type=Path, default=PATHS.processed_dir)
    parser.add_argument("--graphml", type=Path, required=True)
    parser.add_argument("--target-crs", default=DEFAULT_SINGAPORE_CRS)
    parser.add_argument("--write", action="store_true", help="Write sidecar coordinates and update dataset_manifest.json.")
    parser.add_argument("--astar-speed-safety-factor", type=float, default=1.05)
    args = parser.parse_args(argv)
    return augment_coordinates_from_graphml(
        args.processed_dir,
        args.graphml,
        args.target_crs,
        args.write,
        args.astar_speed_safety_factor,
    )


if __name__ == "__main__":
    main()
