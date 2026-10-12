"""Convert a strict raw edge-time table into the frozen Chronos dataset.

The implementation intentionally treats preprocessing as a fail-closed data
boundary.  It validates the complete time-by-edge grid and every value used by
Init before publishing a processed directory.
"""

import contextlib
import hashlib
import io
import json
import math
import os
import shutil
import uuid
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


PREPROCESS_SCHEMA_VERSION = "chronos-processed-edge-time-v1"
REQUIRED_COLUMNS = (
    "time_bucket",
    "osm_edge_id",
    "osm_u",
    "osm_v",
    "speed_kmh",
    "travel_time_sec",
)
EDGE_METADATA_COLUMNS = (
    "osm_edge_id",
    "osm_u",
    "osm_v",
    "osm_key",
    "road_class",
    "osm_length_m",
    "num_lta_links",
    "source",
    "imputation_method",
    "num_imputation_neighbors",
    "confidence",
    "speed_policy",
    "fixture_profile",
)
COORDINATE_COLUMNS = ("osm_u_x", "osm_u_y", "osm_v_x", "osm_v_y")
PROCESSED_FILES = {
    "travel_time": "sg_travel_time.npy",
    "speed": "sg_speed.npy",
    "edge_index": "sg_edge_index.npy",
    "edge_index_node_ids": "sg_edge_index_node_ids.npy",
    "node_ids": "sg_node_ids.npy",
    "edge_metadata": "sg_edge_metadata.parquet",
    "time_buckets": "sg_time_buckets.csv",
}


class PreprocessValidationError(ValueError):
    """Raised when raw or generated data violates the processed-data contract."""


def preprocess_edge_time(
    input_path: Path,
    output_dir: Path,
    *,
    dataset_name: str = "Chronos-SG-2026",
    time_granularity_minutes: int = 15,
    coordinate_crs: Optional[str] = None,
) -> dict:
    """Validate and atomically publish one standard processed dataset.

    ``input_path`` must be CSV or Parquet.  Existing output directories are
    never overwritten; callers that intentionally replace a pipeline stage
    should build elsewhere and atomically promote that directory.
    """

    input_path = Path(input_path).resolve()
    output_dir = Path(output_dir).resolve()
    if not input_path.is_file():
        raise PreprocessValidationError(f"raw_input_not_found:{input_path}")
    if output_dir.exists():
        raise PreprocessValidationError(f"processed_output_already_exists:{output_dir}")
    if int(time_granularity_minutes) <= 0:
        raise PreprocessValidationError("time_granularity_minutes_must_be_positive")
    if not str(dataset_name).strip():
        raise PreprocessValidationError("dataset_name_must_not_be_empty")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = output_dir.parent / (
        "." + output_dir.name + ".preprocess-" + uuid.uuid4().hex
    )
    if staging.exists():
        raise PreprocessValidationError(f"staging_path_already_exists:{staging}")
    staging.mkdir(parents=False)
    try:
        raw = _read_raw_table(input_path)
        normalized = _normalize_and_validate(
            raw,
            time_granularity_minutes=int(time_granularity_minutes),
            coordinate_crs=coordinate_crs,
        )
        artifacts = _write_processed_artifacts(staging, normalized)
        manifest = _build_manifest(
            input_path,
            staging,
            normalized,
            artifacts,
            dataset_name=str(dataset_name),
            time_granularity_minutes=int(time_granularity_minutes),
            coordinate_crs=coordinate_crs,
        )
        _write_json(staging / "dataset_manifest.json", manifest)
        compatibility = _compatibility_validate(staging)
        validation = _build_validation_summary(staging, manifest, compatibility)
        _write_json(staging / "preprocessing_validation.json", validation)
        manifest["validation"] = {
            "status": "valid",
            "summary_file": "preprocessing_validation.json",
            "summary_sha256": _sha256_file(
                staging / "preprocessing_validation.json"
            ),
            "compatibility_validator": "chronos.init.validate_dataset",
        }
        _write_json(staging / "dataset_manifest.json", manifest)
        _verify_manifest_artifacts(staging, manifest)
        os.replace(str(staging), str(output_dir))
        return manifest
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _read_raw_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            frame = pd.read_csv(path)
        elif suffix in {".parquet", ".pq"}:
            frame = pd.read_parquet(path)
        else:
            raise PreprocessValidationError(
                "raw_input_format_must_be_csv_or_parquet"
            )
    except PreprocessValidationError:
        raise
    except ImportError as exc:
        raise PreprocessValidationError(
            "parquet_engine_missing:install_requirements-preprocessing.txt"
        ) from exc
    except Exception as exc:
        raise PreprocessValidationError(f"raw_input_read_failed:{exc}") from exc
    if frame.empty:
        raise PreprocessValidationError("raw_input_is_empty")
    return frame


def _normalize_and_validate(
    frame: pd.DataFrame,
    *,
    time_granularity_minutes: int,
    coordinate_crs: Optional[str],
) -> dict:
    missing_columns = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing_columns:
        raise PreprocessValidationError(
            "missing_required_columns:" + ",".join(missing_columns)
        )
    required_missing = {
        name: int(frame[name].isna().sum())
        for name in REQUIRED_COLUMNS
        if frame[name].isna().any()
    }
    if required_missing:
        raise PreprocessValidationError(
            "missing_required_values:"
            + json.dumps(required_missing, sort_keys=True)
        )

    data = frame.copy()
    try:
        parsed_time = pd.to_datetime(data["time_bucket"], errors="raise", utc=True)
    except Exception as exc:
        raise PreprocessValidationError("time_bucket_parse_failed") from exc
    data["time_bucket"] = parsed_time.dt.tz_convert(None)
    data["osm_edge_id"] = _strict_edge_id(data["osm_edge_id"])
    for name in ("osm_u", "osm_v"):
        data[name] = _strict_int64(data[name], name)
    for name in ("speed_kmh", "travel_time_sec"):
        data[name] = _strict_finite_float(data[name], name, positive=True)

    if "osm_length_m" in data.columns:
        data["osm_length_m"] = _strict_finite_float(
            data["osm_length_m"], "osm_length_m", positive=True
        )

    present_coordinates = tuple(
        name for name in COORDINATE_COLUMNS if name in data.columns
    )
    if present_coordinates and set(present_coordinates) != set(COORDINATE_COLUMNS):
        missing = sorted(set(COORDINATE_COLUMNS) - set(present_coordinates))
        raise PreprocessValidationError(
            "partial_coordinate_schema:" + ",".join(missing)
        )
    coordinates = None
    if present_coordinates:
        _require_projected_crs(coordinate_crs)
        for name in COORDINATE_COLUMNS:
            data[name] = _strict_finite_float(data[name], name, positive=False)

    duplicate_mask = data.duplicated(
        subset=["time_bucket", "osm_edge_id"], keep=False
    )
    if bool(duplicate_mask.any()):
        sample = (
            data.loc[duplicate_mask, ["time_bucket", "osm_edge_id"]]
            .sort_values(["time_bucket", "osm_edge_id"])
            .head(8)
            .astype(str)
            .to_dict("records")
        )
        raise PreprocessValidationError(
            "duplicate_time_edge_rows:" + json.dumps(sample, sort_keys=True)
        )

    time_buckets = pd.DatetimeIndex(
        sorted(data["time_bucket"].unique()), name="time_bucket"
    )
    # Chronos-SG uses strings such as ``u_v_key``.  Numeric fixtures are
    # normalized to decimal strings so that one stable lexicographic contract
    # covers both inputs without lossy integer assumptions.
    edge_ids = pd.Index(sorted(data["osm_edge_id"].unique()), name="osm_edge_id")
    if not len(time_buckets) or not len(edge_ids):
        raise PreprocessValidationError("time_or_edge_axis_is_empty")
    expected_rows = int(len(time_buckets) * len(edge_ids))
    if len(data) != expected_rows:
        observed = data.groupby("time_bucket")["osm_edge_id"].nunique()
        raise PreprocessValidationError(
            "incomplete_time_edge_grid:"
            + json.dumps(
                {
                    "rows": int(len(data)),
                    "expected_rows": expected_rows,
                    "min_edges_per_bucket": int(observed.min()),
                    "max_edges_per_bucket": int(observed.max()),
                },
                sort_keys=True,
            )
        )
    _validate_time_axis(time_buckets, int(time_granularity_minutes))

    metadata_columns = [
        name for name in EDGE_METADATA_COLUMNS if name in data.columns
    ]
    _validate_edge_metadata_stability(data, metadata_columns)
    edge_metadata = (
        data.sort_values(["osm_edge_id", "time_bucket"], kind="mergesort")
        .drop_duplicates("osm_edge_id", keep="first")[metadata_columns]
        .sort_values("osm_edge_id", kind="mergesort")
        .reset_index(drop=True)
    )
    if tuple(edge_metadata["osm_edge_id"].tolist()) != tuple(edge_ids.tolist()):
        raise PreprocessValidationError("edge_metadata_axis_mismatch")

    node_ids = pd.Index(
        sorted(
            set(edge_metadata["osm_u"].astype("int64"))
            | set(edge_metadata["osm_v"].astype("int64"))
        ),
        name="osm_node_id",
    )
    node_to_idx = {int(node): index for index, node in enumerate(node_ids)}
    edge_index_node_ids = (
        edge_metadata[["osm_u", "osm_v"]].astype("int64").to_numpy().T
    )
    edge_index = np.vstack(
        (
            edge_metadata["osm_u"].map(node_to_idx).to_numpy(dtype=np.int64),
            edge_metadata["osm_v"].map(node_to_idx).to_numpy(dtype=np.int64),
        )
    )

    time_to_idx = {value: index for index, value in enumerate(time_buckets)}
    edge_to_idx = {str(value): index for index, value in enumerate(edge_ids)}
    travel_time = _dense_matrix(
        data, "travel_time_sec", time_to_idx, edge_to_idx
    )
    speed = _dense_matrix(data, "speed_kmh", time_to_idx, edge_to_idx)
    if present_coordinates:
        coordinates = _build_node_coordinates(data, node_ids)

    source_profiles = tuple(
        sorted(
            str(value)
            for value in data.get("fixture_profile", pd.Series(dtype=str))
            .dropna()
            .unique()
        )
    )
    if len(source_profiles) > 1:
        raise PreprocessValidationError("fixture_profile_must_be_constant")
    optional_missing = {
        name: int(data[name].isna().sum())
        for name in metadata_columns
        if data[name].isna().any()
    }
    return {
        "frame": data,
        "time_buckets": time_buckets,
        "edge_ids": edge_ids,
        "edge_metadata": edge_metadata,
        "node_ids": node_ids.to_numpy(dtype=np.int64),
        "edge_index": edge_index.astype(np.int64),
        "edge_index_node_ids": edge_index_node_ids.astype(np.int64),
        "travel_time": travel_time,
        "speed": speed,
        "node_coordinates": coordinates,
        "source_profile": source_profiles[0] if source_profiles else None,
        "optional_missing": optional_missing,
    }


def _strict_int64(series: pd.Series, name: str) -> pd.Series:
    try:
        numeric = pd.to_numeric(series, errors="raise")
    except Exception as exc:
        raise PreprocessValidationError(f"{name}_must_be_integer") from exc
    values = numeric.to_numpy(dtype=np.float64)
    if not np.all(np.isfinite(values)):
        raise PreprocessValidationError(f"{name}_contains_non_finite_values")
    if not np.all(values == np.floor(values)):
        raise PreprocessValidationError(f"{name}_must_be_integer")
    limits = np.iinfo(np.int64)
    if np.any(values < limits.min) or np.any(values > limits.max):
        raise PreprocessValidationError(f"{name}_outside_int64_range")
    return pd.Series(values.astype(np.int64), index=series.index, name=series.name)


def _strict_edge_id(series: pd.Series) -> pd.Series:
    def normalize(value) -> str:
        if isinstance(value, (bool, np.bool_)):
            raise PreprocessValidationError("osm_edge_id_boolean_not_allowed")
        if isinstance(value, (int, np.integer)):
            return str(int(value))
        if isinstance(value, (float, np.floating)):
            if not np.isfinite(float(value)) or float(value) != math.floor(float(value)):
                raise PreprocessValidationError(
                    "osm_edge_id_numeric_value_must_be_finite_integer"
                )
            return str(int(value))
        text = str(value).strip()
        if not text:
            raise PreprocessValidationError("osm_edge_id_must_not_be_empty")
        return text

    try:
        values = [normalize(value) for value in series.tolist()]
    except PreprocessValidationError:
        raise
    except Exception as exc:
        raise PreprocessValidationError("osm_edge_id_normalization_failed") from exc
    return pd.Series(values, index=series.index, name=series.name, dtype="string")


def _strict_finite_float(
    series: pd.Series, name: str, *, positive: bool
) -> pd.Series:
    try:
        numeric = pd.to_numeric(series, errors="raise").astype(np.float64)
    except Exception as exc:
        raise PreprocessValidationError(f"{name}_must_be_numeric") from exc
    values = numeric.to_numpy(dtype=np.float64)
    if not np.all(np.isfinite(values)):
        raise PreprocessValidationError(f"{name}_contains_non_finite_values")
    if positive and np.any(values <= 0.0):
        raise PreprocessValidationError(f"{name}_must_be_positive")
    return pd.Series(values, index=series.index, name=series.name)


def _validate_time_axis(
    time_buckets: pd.DatetimeIndex, time_granularity_minutes: int
) -> None:
    if len(time_buckets) <= 1:
        return
    values = time_buckets.view("int64")
    deltas = np.diff(values)
    expected = int(time_granularity_minutes) * 60 * 1_000_000_000
    if np.any(deltas != expected):
        unique_seconds = sorted(
            {float(value) / 1_000_000_000.0 for value in deltas}
        )
        raise PreprocessValidationError(
            "irregular_time_axis:"
            + json.dumps(
                {
                    "expected_seconds": int(time_granularity_minutes) * 60,
                    "observed_seconds": unique_seconds[:8],
                },
                sort_keys=True,
            )
        )


def _validate_edge_metadata_stability(
    data: pd.DataFrame, metadata_columns: Sequence[str]
) -> None:
    for name in metadata_columns:
        if name == "osm_edge_id":
            continue
        counts = data.groupby("osm_edge_id", sort=True, dropna=False)[name].nunique(
            dropna=False
        )
        unstable = counts[counts > 1]
        if len(unstable):
            raise PreprocessValidationError(
                f"edge_metadata_not_constant:{name}:"
                + ",".join(str(item) for item in unstable.index[:8])
            )


def _dense_matrix(
    data: pd.DataFrame,
    value_column: str,
    time_to_idx: Dict[pd.Timestamp, int],
    edge_to_idx: Dict[str, int],
) -> np.ndarray:
    matrix = np.full(
        (len(time_to_idx), len(edge_to_idx)), np.nan, dtype=np.float32
    )
    rows = data["time_bucket"].map(time_to_idx).to_numpy(dtype=np.int64)
    columns = data["osm_edge_id"].map(edge_to_idx).to_numpy(dtype=np.int64)
    matrix[rows, columns] = data[value_column].to_numpy(dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape != (
        len(time_to_idx),
        len(edge_to_idx),
    ):
        raise PreprocessValidationError(f"{value_column}_matrix_shape_invalid")
    if not np.all(np.isfinite(matrix)):
        missing = int(np.count_nonzero(~np.isfinite(matrix)))
        raise PreprocessValidationError(
            f"{value_column}_matrix_has_missing_cells:{missing}"
        )
    if np.any(matrix <= 0.0):
        raise PreprocessValidationError(
            f"{value_column}_matrix_must_be_positive"
        )
    return matrix


def _build_node_coordinates(
    data: pd.DataFrame, node_ids: pd.Index
) -> np.ndarray:
    source = data[["osm_u", "osm_u_x", "osm_u_y"]].rename(
        columns={"osm_u": "node", "osm_u_x": "x", "osm_u_y": "y"}
    )
    target = data[["osm_v", "osm_v_x", "osm_v_y"]].rename(
        columns={"osm_v": "node", "osm_v_x": "x", "osm_v_y": "y"}
    )
    rows = pd.concat((source, target), ignore_index=True)
    for name in ("x", "y"):
        counts = rows.groupby("node", sort=True)[name].nunique(dropna=False)
        unstable = counts[counts != 1]
        if len(unstable):
            raise PreprocessValidationError(
                "node_coordinate_not_unique:"
                + ",".join(str(int(item)) for item in unstable.index[:8])
            )
    first = rows.drop_duplicates("node").set_index("node")
    missing = [int(node) for node in node_ids if int(node) not in first.index]
    if missing:
        raise PreprocessValidationError(
            "node_coordinates_missing:" + ",".join(map(str, missing[:8]))
        )
    coordinates = np.asarray(
        [[first.loc[int(node), "x"], first.loc[int(node), "y"]] for node in node_ids],
        dtype=np.float64,
    )
    if coordinates.shape != (len(node_ids), 2):
        raise PreprocessValidationError("node_coordinate_shape_invalid")
    if not np.all(np.isfinite(coordinates)):
        raise PreprocessValidationError("node_coordinates_contain_non_finite_values")
    return coordinates


def _require_projected_crs(coordinate_crs: Optional[str]) -> None:
    value = str(coordinate_crs or "").strip()
    if not value:
        raise PreprocessValidationError(
            "coordinate_crs_required_when_coordinate_columns_are_present"
        )
    normalized = value.upper().replace(" ", "")
    if any(marker in normalized for marker in ("EPSG:4326", "WGS84", "CRS84")):
        raise PreprocessValidationError(
            "coordinate_crs_must_be_projected_not_geographic"
        )


def _write_processed_artifacts(staging: Path, normalized: dict) -> dict:
    np.save(staging / PROCESSED_FILES["travel_time"], normalized["travel_time"])
    np.save(staging / PROCESSED_FILES["speed"], normalized["speed"])
    np.save(staging / PROCESSED_FILES["edge_index"], normalized["edge_index"])
    np.save(
        staging / PROCESSED_FILES["edge_index_node_ids"],
        normalized["edge_index_node_ids"],
    )
    np.save(staging / PROCESSED_FILES["node_ids"], normalized["node_ids"])
    edge_metadata = normalized["edge_metadata"].copy()
    edge_metadata.insert(
        0, "edge_idx", np.arange(len(edge_metadata), dtype=np.int64)
    )
    try:
        edge_metadata.to_parquet(
            staging / PROCESSED_FILES["edge_metadata"], index=False
        )
    except ImportError as exc:
        raise PreprocessValidationError(
            "parquet_engine_missing:install_requirements-preprocessing.txt"
        ) from exc
    pd.DataFrame(
        {
            "time_idx": np.arange(
                len(normalized["time_buckets"]), dtype=np.int64
            ),
            "time_bucket": normalized["time_buckets"],
        }
    ).to_csv(
        staging / PROCESSED_FILES["time_buckets"],
        index=False,
        encoding="utf-8",
        date_format="%Y-%m-%dT%H:%M:%S",
        lineterminator="\n",
    )
    files = dict(PROCESSED_FILES)
    if normalized["node_coordinates"] is not None:
        files["node_coordinates"] = "sg_node_coordinates.npy"
        np.save(
            staging / files["node_coordinates"],
            normalized["node_coordinates"],
        )
    return files


def _build_manifest(
    input_path: Path,
    staging: Path,
    normalized: dict,
    files: dict,
    *,
    dataset_name: str,
    time_granularity_minutes: int,
    coordinate_crs: Optional[str],
) -> dict:
    file_hashes = {
        name: _sha256_file(staging / relative)
        for name, relative in sorted(files.items())
    }
    time_buckets = normalized["time_buckets"]
    travel = normalized["travel_time"]
    speed = normalized["speed"]
    edge_index = normalized["edge_index"]
    self_loops = int(np.count_nonzero(edge_index[0] == edge_index[1]))
    parallel_pairs = len(edge_index.T) - len(
        {tuple(map(int, item)) for item in edge_index.T}
    )
    source = {
        "path": str(input_path),
        "format": input_path.suffix.lower().lstrip("."),
        "size_bytes": int(input_path.stat().st_size),
        "sha256": _sha256_file(input_path),
    }
    fingerprint_payload = {
        "schema_version": PREPROCESS_SCHEMA_VERSION,
        "source_sha256": source["sha256"],
        "files": file_hashes,
        "shape": {
            "time_buckets": int(travel.shape[0]),
            "edges": int(travel.shape[1]),
            "nodes": int(len(normalized["node_ids"])),
        },
    }
    manifest = {
        "schema_version": PREPROCESS_SCHEMA_VERSION,
        "dataset": str(dataset_name),
        "source": source,
        "source_profile": normalized["source_profile"],
        "num_time_buckets": int(travel.shape[0]),
        "num_edges": int(travel.shape[1]),
        "num_nodes": int(len(normalized["node_ids"])),
        "num_records": int(normalized["frame"].shape[0]),
        "time_start": pd.Timestamp(time_buckets.min()).isoformat(),
        "time_end": pd.Timestamp(time_buckets.max()).isoformat(),
        "time_granularity_minutes": int(time_granularity_minutes),
        "time_normalization": "parsed_as_utc_then_stored_timezone_naive",
        "array_layout": "travel_time[time_idx,edge_idx],speed[time_idx,edge_idx]",
        "array_dtype": {
            "travel_time": str(travel.dtype),
            "speed": str(speed.dtype),
            "edge_index": str(edge_index.dtype),
            "node_ids": str(normalized["node_ids"].dtype),
        },
        "files": dict(files),
        "file_sha256": file_hashes,
        "fingerprint": _fingerprint_json(fingerprint_payload),
        "statistics": {
            "travel_time_sec": _numeric_summary(travel),
            "speed_kmh": _numeric_summary(speed),
            "self_loop_count": self_loops,
            "parallel_directed_pair_extra_edge_count": int(parallel_pairs),
            "optional_metadata_missing_counts": normalized["optional_missing"],
            "complete_time_edge_grid": True,
            "duplicate_time_edge_row_count": 0,
        },
    }
    if normalized["node_coordinates"] is not None:
        coordinates = normalized["node_coordinates"]
        manifest.update(
            {
                "coordinate_crs": str(coordinate_crs),
                "coordinate_units": "projected",
                "coordinate_node_id_match_ratio": 1.0,
                "coordinate_bounds": {
                    "min_x": float(np.min(coordinates[:, 0])),
                    "max_x": float(np.max(coordinates[:, 0])),
                    "min_y": float(np.min(coordinates[:, 1])),
                    "max_y": float(np.max(coordinates[:, 1])),
                },
            }
        )
    return manifest


def _compatibility_validate(processed_dir: Path) -> dict:
    from chronos.init.validate_dataset import validate_processed_dataset

    capture = io.StringIO()
    try:
        with contextlib.redirect_stdout(capture):
            summary = validate_processed_dataset(processed_dir)
    except Exception as exc:
        raise PreprocessValidationError(
            f"processed_dataset_compatibility_validation_failed:{exc}"
        ) from exc
    required = (
        "num_nodes",
        "num_edges",
        "num_buckets",
        "finite_travel_time_ratio",
        "dataset_fingerprint",
    )
    if any(name not in summary for name in required):
        raise PreprocessValidationError(
            "processed_dataset_compatibility_summary_incomplete"
        )
    if not math.isclose(
        float(summary["finite_travel_time_ratio"]), 1.0, rel_tol=0, abs_tol=0
    ):
        raise PreprocessValidationError(
            "processed_dataset_compatibility_non_finite_values"
        )
    return summary


def _build_validation_summary(
    staging: Path, manifest: dict, compatibility: dict
) -> dict:
    files = dict(manifest["files"])
    checks = {
        "manifest_counts_match_arrays": (
            int(compatibility["num_nodes"]) == int(manifest["num_nodes"])
            and int(compatibility["num_edges"]) == int(manifest["num_edges"])
            and int(compatibility["num_buckets"])
            == int(manifest["num_time_buckets"])
        ),
        "all_values_finite": float(
            compatibility["finite_travel_time_ratio"]
        )
        == 1.0,
        "edge_index_shape_valid": np.load(
            staging / files["edge_index"], mmap_mode="r"
        ).shape
        == (2, int(manifest["num_edges"])),
        "time_edge_shape_valid": np.load(
            staging / files["travel_time"], mmap_mode="r"
        ).shape
        == (
            int(manifest["num_time_buckets"]),
            int(manifest["num_edges"]),
        ),
        "file_hashes_match": all(
            _sha256_file(staging / files[name]) == expected
            for name, expected in manifest["file_sha256"].items()
        ),
    }
    if not all(checks.values()):
        failed = sorted(name for name, value in checks.items() if not value)
        raise PreprocessValidationError(
            "processed_dataset_summary_checks_failed:" + ",".join(failed)
        )
    return {
        "schema_version": "chronos-preprocessing-validation-v1",
        "status": "valid",
        "checks": checks,
        "compatibility_summary": compatibility,
        "manifest_fingerprint": manifest["fingerprint"],
    }


def _verify_manifest_artifacts(directory: Path, manifest: dict) -> None:
    errors = []
    for name, relative in sorted(manifest.get("files", {}).items()):
        path = directory / relative
        if not path.is_file():
            errors.append(f"missing:{name}")
            continue
        expected = str(manifest.get("file_sha256", {}).get(name, ""))
        if not expected or _sha256_file(path) != expected:
            errors.append(f"sha256:{name}")
    validation = dict(manifest.get("validation") or {})
    summary = directory / str(validation.get("summary_file", ""))
    if not summary.is_file():
        errors.append("missing:preprocessing_validation")
    elif _sha256_file(summary) != validation.get("summary_sha256"):
        errors.append("sha256:preprocessing_validation")
    if errors:
        raise PreprocessValidationError(
            "processed_artifact_verification_failed:" + ",".join(errors)
        )


def _numeric_summary(values: np.ndarray) -> dict:
    array = np.asarray(values, dtype=np.float64)
    return {
        "min": float(np.min(array)),
        "max": float(np.max(array)),
        "mean": float(np.mean(array)),
        "finite_count": int(np.count_nonzero(np.isfinite(array))),
        "value_count": int(array.size),
    }


def _fingerprint_json(value) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))
