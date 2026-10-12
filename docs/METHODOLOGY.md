# 📖 Chronos-SG-2026: Dataset Construction Methodology

## 1. Purpose and data model

Chronos-SG-2026 is a directed, time-dependent driving network constructed for privacy-preserving multi-stop navigation with temporal constraints. The network contains **24,206 nodes**, **46,070 directed edges**, and **96 fifteen-minute intervals** in a daily temporal profile.

A directed graph is represented as `G = (V, E)`. For each directed edge `e` and traffic bin `b`, the dataset stores positive finite values `speed[e,b]` (km/h) and `travel_time[e,b]` (seconds). Opposite driving directions have separate records; parallel edges remain distinguishable by stable identifiers. The online routing layer interprets costs at the time each edge is entered.

## 2. Source alignment

The road network is based on OpenStreetMap's Singapore driving graph. Traffic-speed observations come from the Singapore Land Transport Authority's DataMall service. The construction pipeline pairs traffic road links with directed road edges and aggregates the aligned observations to **15-minute** bins. The alignment result is recorded as an edge-time table, and optional provenance fields identify observation coverage and source metadata.

The published processor starts from this **aligned edge-time table**; the collection and link-matching inputs can be archived separately with the original dataset release. No credentials are required to transform an already aligned file into the published array format.

## 3. Temporal normalization

`chronos.preprocess.processor.preprocess_edge_time` parses timestamps, enforces one record per `(time_bucket, osm_edge_id)`, checks an uninterrupted 15-minute series, and requires a complete directed-edge × time-bin grid. Each row must have finite, strictly positive speed and traversal time. Edge endpoints and per-edge metadata must remain stable across all bins.

The temporal profile is **96 × 46,070 = 4,422,720 aligned edge-bin entries** in the study configuration. This count describes the normalized dense representation, not the number of raw API observations.

## 4. Graph arrays and coordinates

Stable OSM IDs are mapped to dense node indices. Edges are mapped to directed `[2, |E|]` index arrays. The speed and travel-time matrices each have shape `[96, |E|]`. The OSM GraphML provides longitude/latitude, and `chronos.init.coordinates` projects every matched node to **EPSG:3414**, a metric coordinate system. The validation procedure checks complete coordinate coverage when graph projection is requested.

## 5. Dataset consistency and provenance

Dataset construction records the input path, schema version, edge/time dimensions, and hashes in `dataset_manifest.json`. Structural and numeric checks are recorded in `preprocessing_validation.json`. Output is built in a temporary directory and published only after successful validation, with explicit refusal to overwrite an existing release.

## 6. Reproduction procedure

```powershell
python -m dataset.build from-edge-time `
  --input data/singapore/traffic/edge_speed_timeseries_15min.parquet `
  --graphml data/singapore/raw/singapore_drive.graphml `
  --output data/singapore/processed/full `
  --expect-nodes 24206 --expect-edges 46070 --expect-buckets 96

python -m dataset.build validate --processed data/singapore/processed/full
```

If projected coordinates are already included in the aligned input, use `--coordinate-crs EPSG:3414` **instead of** `--graphml`. For a fresh release, archive the processed dataset with `python -m dataset.package_release` and retain its SHA-256 and manifest alongside the released version.

## 7. Research use

The dataset supports edge-entry-time-dependent routing, controlled traffic variation across the day, multi-stop navigation queries with arrival constraints, and road-event experiments. It is a road-data component of the Chronos system, not a replacement for the system's separately generated encrypted offline indexes.

## 8. Credits

© OpenStreetMap contributors (ODbL); Singapore Land Transport Authority DataMall. Confirm applicable redistribution terms before publishing raw or derived records. See `NOTICE.md`.
