"""Generate a deterministic raw edge-time fixture for full-pipeline tests."""

import argparse
import hashlib
import json
import os
from pathlib import Path

import pandas as pd


MICRO_FIXTURE_PROFILE = "chronos-deterministic-micro-v1"
MICRO_COORDINATE_CRS = "LOCAL:CHRONOS_MICRO_METERS"
MICRO_RAW_MANIFEST_SCHEMA = "chronos-micro-raw-manifest-v2"
MICRO_GENERATOR_ID = "chronos.preprocess.generate_micro_raw"
MICRO_GENERATOR_VERSION = 2


def generate_micro_raw(output_path: Path, manifest_path: Path = None) -> dict:
    """Write the same small directed road network for every invocation."""

    output_path = Path(output_path)
    if output_path.suffix.lower() not in {".csv", ".parquet", ".pq"}:
        raise ValueError("micro_raw_output_must_be_csv_or_parquet")
    if output_path.exists():
        raise ValueError(f"micro_raw_output_already_exists:{output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame = build_micro_raw_frame()
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    if output_path.suffix.lower() == ".csv":
        frame.to_csv(
            temporary,
            index=False,
            encoding="utf-8",
            date_format="%Y-%m-%dT%H:%M:%S",
            lineterminator="\n",
        )
    else:
        frame.to_parquet(temporary, index=False)
    os.replace(str(temporary), str(output_path))
    payload = {
        "schema_version": MICRO_RAW_MANIFEST_SCHEMA,
        "generator_id": MICRO_GENERATOR_ID,
        "generator_version": MICRO_GENERATOR_VERSION,
        "fixture_profile": MICRO_FIXTURE_PROFILE,
        "coordinate_crs": MICRO_COORDINATE_CRS,
        "raw_file": output_path.name,
        "raw_sha256": _sha256_file(output_path),
        "canonical_structure_sha256": micro_structure_fingerprint(frame),
        "row_count": int(len(frame)),
        "edge_count": int(frame["osm_edge_id"].nunique()),
        "time_bucket_count": int(frame["time_bucket"].nunique()),
        "node_ids": [100, 110, 120, 130],
        "processed_node_indices": {
            "source": 0,
            "stops": [1, 2],
            "target": 3,
        },
        "partial_orders": [[1, 2]],
    }
    manifest_path = (
        Path(manifest_path)
        if manifest_path is not None
        else output_path.with_suffix(output_path.suffix + ".manifest.json")
    )
    if manifest_path.exists():
        raise ValueError(f"micro_raw_manifest_already_exists:{manifest_path}")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def validate_generated_micro_raw(
    raw_path: Path,
    manifest_path: Path,
) -> dict:
    """Validate generator sidecar, raw digest, and the exact micro structure.

    The returned evidence is suitable only for the pipeline invocation that
    used ``--generate-micro-raw``.  Merely putting ``fixture_profile`` in an
    arbitrary input table never calls this validator and grants no portable
    Init capability.
    """

    raw_path = Path(raw_path).resolve()
    manifest_path = Path(manifest_path).resolve()
    if not raw_path.is_file() or not manifest_path.is_file():
        raise ValueError("generated_micro_input_or_sidecar_missing")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError("generated_micro_sidecar_invalid_json") from exc
    expected_fields = {
        "schema_version": MICRO_RAW_MANIFEST_SCHEMA,
        "generator_id": MICRO_GENERATOR_ID,
        "generator_version": MICRO_GENERATOR_VERSION,
        "fixture_profile": MICRO_FIXTURE_PROFILE,
        "coordinate_crs": MICRO_COORDINATE_CRS,
        "raw_file": raw_path.name,
    }
    for name, expected in expected_fields.items():
        if payload.get(name) != expected:
            raise ValueError("generated_micro_sidecar_field_mismatch:" + name)
    raw_sha256 = _sha256_file(raw_path)
    if payload.get("raw_sha256") != raw_sha256:
        raise ValueError("generated_micro_raw_sha256_mismatch")

    actual = _read_generated_frame(raw_path)
    expected = build_micro_raw_frame()
    _assert_exact_micro_structure(actual, expected)
    structure_sha256 = micro_structure_fingerprint(actual)
    canonical_sha256 = micro_structure_fingerprint(expected)
    if structure_sha256 != canonical_sha256:
        raise ValueError("generated_micro_structure_fingerprint_mismatch")
    if payload.get("canonical_structure_sha256") != canonical_sha256:
        raise ValueError(
            "generated_micro_sidecar_structure_fingerprint_mismatch"
        )
    if int(payload.get("row_count", -1)) != len(expected):
        raise ValueError("generated_micro_sidecar_row_count_mismatch")
    if int(payload.get("edge_count", -1)) != 6:
        raise ValueError("generated_micro_sidecar_edge_count_mismatch")
    if int(payload.get("time_bucket_count", -1)) != 4:
        raise ValueError("generated_micro_sidecar_bucket_count_mismatch")
    if payload.get("node_ids") != [100, 110, 120, 130]:
        raise ValueError("generated_micro_sidecar_node_ids_mismatch")

    return {
        "schema_version": "chronos-portable-fixture-evidence-v1",
        "origin": "pipeline_cli_generate_micro_raw",
        "generator_id": MICRO_GENERATOR_ID,
        "generator_version": MICRO_GENERATOR_VERSION,
        "fixture_profile": MICRO_FIXTURE_PROFILE,
        "raw_path": str(raw_path),
        "raw_sha256": raw_sha256,
        "sidecar_path": str(manifest_path),
        "sidecar_sha256": _sha256_file(manifest_path),
        "canonical_structure_sha256": canonical_sha256,
        "strict_structure_validated": True,
    }


def build_micro_raw_frame() -> pd.DataFrame:
    """Return the canonical four-node, six-edge, four-bucket raw table."""

    coordinates = {
        100: (0.0, 0.0),
        110: (200.0, 80.0),
        120: (400.0, -60.0),
        130: (600.0, 0.0),
    }
    edges = (
        (1000, 100, 110, 220.0, 22.0),
        (1001, 110, 120, 245.0, 25.0),
        (1002, 120, 130, 215.0, 21.0),
        (1003, 100, 120, 430.0, 39.0),
        (1004, 110, 130, 425.0, 38.0),
        (1005, 130, 100, 610.0, 55.0),
    )
    buckets = pd.date_range(
        "2026-01-01T00:00:00", periods=4, freq="15min"
    )
    multipliers = (1.0, 1.25, 0.9, 1.1)
    records = []
    for time_bucket, multiplier in zip(buckets, multipliers):
        for edge_id, source, target, length_m, base_time in edges:
            travel_time = float(base_time * multiplier)
            speed_kmh = float(length_m / travel_time * 3.6)
            ux, uy = coordinates[source]
            vx, vy = coordinates[target]
            records.append(
                {
                    "time_bucket": time_bucket,
                    "osm_edge_id": edge_id,
                    "osm_u": source,
                    "osm_v": target,
                    "osm_key": 0,
                    "road_class": "micro",
                    "osm_length_m": length_m,
                    "num_lta_links": 1,
                    "source": "deterministic-generator",
                    "imputation_method": "none",
                    "num_imputation_neighbors": 0,
                    "confidence": 1.0,
                    "speed_policy": "derived_from_length_and_time",
                    "speed_kmh": speed_kmh,
                    "travel_time_sec": travel_time,
                    "osm_u_x": ux,
                    "osm_u_y": uy,
                    "osm_v_x": vx,
                    "osm_v_y": vy,
                    "fixture_profile": MICRO_FIXTURE_PROFILE,
                }
            )
    return pd.DataFrame.from_records(records).sort_values(
        ["time_bucket", "osm_edge_id"], kind="mergesort", ignore_index=True
    )


def micro_structure_fingerprint(frame: pd.DataFrame) -> str:
    """Return a stable content fingerprint independent of CSV/Parquet bytes."""

    normalized = _normalize_micro_frame(frame)
    records = []
    for record in normalized.to_dict("records"):
        encoded = {}
        for name, value in record.items():
            if name == "time_bucket":
                encoded[name] = pd.Timestamp(value).isoformat()
            elif isinstance(value, float):
                # CSV round-tripping may change the last binary ulp. Twelve
                # significant digits are well below the strict 1e-12
                # structure comparison while remaining deterministic.
                encoded[name] = format(value, ".12g")
            elif hasattr(value, "item"):
                encoded[name] = value.item()
            else:
                encoded[name] = value
        records.append(encoded)
    payload = json.dumps(
        {
            "columns": list(normalized.columns),
            "records": records,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic raw Chronos edge-time fixture."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    payload = generate_micro_raw(args.output, args.manifest)
    print(
        json.dumps(
            payload,
            sort_keys=True,
            indent=None if args.compact else 2,
        )
    )
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_generated_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    raise ValueError("generated_micro_raw_format_invalid")


def _normalize_micro_frame(frame: pd.DataFrame) -> pd.DataFrame:
    expected_columns = tuple(build_micro_raw_frame().columns)
    if tuple(frame.columns) != expected_columns:
        raise ValueError("generated_micro_columns_mismatch")
    normalized = frame.copy()
    try:
        normalized["time_bucket"] = pd.to_datetime(
            normalized["time_bucket"],
            errors="raise",
        )
    except Exception as exc:
        raise ValueError("generated_micro_time_bucket_invalid") from exc
    return normalized.sort_values(
        ["time_bucket", "osm_edge_id"],
        kind="mergesort",
        ignore_index=True,
    )


def _assert_exact_micro_structure(
    actual: pd.DataFrame,
    expected: pd.DataFrame,
) -> None:
    actual = _normalize_micro_frame(actual)
    expected = _normalize_micro_frame(expected)
    try:
        pd.testing.assert_frame_equal(
            actual,
            expected,
            check_dtype=False,
            check_exact=False,
            rtol=1e-12,
            atol=1e-12,
        )
    except AssertionError as exc:
        raise ValueError("generated_micro_raw_not_canonical") from exc


if __name__ == "__main__":
    main()
