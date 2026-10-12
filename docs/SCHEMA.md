# 🗂️ Processed Dataset Schema

Generated under `data/singapore/processed/full/`:

| File | Role | Expected shape / representation |
| :-- | :-- | :-- |
| `sg_travel_time.npy` | Directed edge traversal times (seconds) | `float32 [96, 46070]` |
| `sg_speed.npy` | Edge speeds (km/h) | `float32 [96, 46070]` |
| `sg_edge_index.npy` | Dense directed graph adjacency | `int [2, 46070]` |
| `sg_edge_index_node_ids.npy` | Original OSM endpoint IDs | `int [2, 46070]` |
| `sg_node_ids.npy` | OSM node ID → dense index ordering | length `24206` |
| `sg_node_coordinates.npy` | Projected metric coordinates | `[24206, 2]`, EPSG:3414 |
| `sg_edge_metadata.parquet` | Edge identifiers and metadata | 46,070 rows |
| `sg_time_buckets.csv` | Ordered time-bin descriptors | 96 rows |
| `dataset_manifest.json` | Data lineage and file hashes | JSON |
| `preprocessing_validation.json` | Validation summary | JSON |

The listed dimensions are for the frozen study version. The same code also accepts other valid road networks if the `--expect-*` checks are adjusted or omitted.

Input requires `time_bucket`, `osm_edge_id`, `osm_u`, `osm_v`, `speed_kmh`, and `travel_time_sec`. Road-edge ID ordering is deterministic across all output arrays. Data validation should be run whenever a released archive is restored.
