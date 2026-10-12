# 🗺️ Chronos-SG-2026 Dataset

**A time-dependent directed road-network dataset for secure multi-stop navigation.**

[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://www.python.org/)
![Network](https://img.shields.io/badge/Network-Singapore-1f6feb)
![Granularity](https://img.shields.io/badge/Temporal%20resolution-15%20min-brightgreen)

This repository contains the **dataset processing, validation, and packaging pipeline** for *Chronos-SG-2026*, used in the Chronos secure semi-constrained navigation study. Data preparation is separated from the navigation experiments so that dataset releases can be independently versioned and verified.

## 📌 Dataset at a glance

| Property | Specification |
| :-- | :-- |
| Region | Singapore driving road network |
| Road topology | OpenStreetMap (OSM) |
| Traffic observations | LTA DataMall Traffic Speed Bands |
| Nodes | **24,206** |
| Directed road edges | **46,070** |
| Time bins | **96** per daily profile |
| Resolution | **15 minutes** |
| Spatial reference | **EPSG:3414** (meters) |
| Final representation | Time-dependent edge weights + directed graph arrays |

The **24,206 / 46,070 / 96** values describe the frozen paper dataset. Rebuilding with newly collected traffic observations yields a separately versioned dataset, even when the shapes match.

## ⚙️ 1. Installation

**Windows PowerShell — Python 3.11:**

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

## 🧭 2. Construction pipeline

```text
OSM directed driving graph           LTA traffic speed observations
              │                                 │
              └────────── road-link alignment ──┘
                                  │
                     15-minute edge-time table
                                  │
                   Temporal/structural validation
                                  │
                    Dense directed edge arrays
                                  │
                   EPSG:3414 node projection
                                  │
                     Dataset manifest + hashes
```

The processing code consumes a **road-link-aligned edge-time table** and the corresponding OSM GraphML. The upstream collection/alignment stage provides these input files; the operations implemented here start from those aligned inputs. See [Methodology](docs/METHODOLOGY.md) for the data model and preprocessing details.

### Input layout

```text
data/singapore/
  raw/singapore_drive.graphml
  traffic/edge_speed_timeseries_15min.parquet
  processed/full/                       # generated output
```

The aligned input file can be `.parquet` or `.csv`. It contains one record per directed edge and 15-minute time bucket with the following **required fields**:

| Field | Meaning |
| :-- | :-- |
| `time_bucket` | Timestamp of the corresponding time bin |
| `osm_edge_id` | Stable directed edge identifier |
| `osm_u`, `osm_v` | Directed OSM node identifiers |
| `speed_kmh` | Edge speed, kilometers per hour |
| `travel_time_sec` | Edge traversal cost, seconds |

Relevant metadata (edge length, road class, matched-link provenance, etc.) is preserved when supplied. Use the original frozen aligned input to recreate the study dataset exactly.

### Run the full dataset processor

```powershell
python -m dataset.build from-edge-time `
  --input data/singapore/traffic/edge_speed_timeseries_15min.parquet `
  --graphml data/singapore/raw/singapore_drive.graphml `
  --output data/singapore/processed/full `
  --expect-nodes 24206 --expect-edges 46070 --expect-buckets 96
```

On success, the command prints a JSON object with `"status": "PASS"`, the resolved output directory, graph dimensions, and its dataset fingerprint. An existing output directory is never silently overwritten.

### Validate a frozen dataset

```powershell
python -m dataset.build validate --processed data/singapore/processed/full
```

## 🧪 3. Quick test (no road-data download)

```powershell
python -m dataset.build micro --run-dir runs/micro-data
python -m dataset.build validate --processed runs/micro-data/processed
```

This creates a deterministic **4-node, 6-directed-edge, 4-bin** example for testing the processing format. It is an interface check, not the paper dataset.

## 📦 4. Dataset release

Create an independent ZIP, file manifest, and SHA-256 checksum from the **processed** dataset:

```powershell
python -m dataset.package_release `
  --processed-dir data/singapore/processed/full `
  --output ..\Chronos-SG-2026-processed.zip --verify
```

The command prints the complete output path and integrity hash. Publish the dataset archive as a **GitHub Release asset**, with the release version and fingerprint identified in your experiment repository. Review [Attribution and redistribution](NOTICE.md) before distributing third-party inputs.

## 🔍 5. Find your existing local data (no 374-GB upload)

For an existing Windows installation, create a concise file inventory without copying any datasets or recursively scanning large offline caches:

```powershell
python -m tools.inventory `
  --data-dir "D:\Chronus\shengzhizhihou\data\singapore" `
  --cache-dir "D:\Chronus\shengzhizhihou\artifacts\singapore"
```

**Results are written to `reports/` under the repository root, and their absolute paths are printed.** Upload only these small CSV/JSON summaries when reviewing the local dataset layout. Use `-h` to change the output location.

## 📁 Repository layout

```text
dataset/                 # Build, validation, and release CLIs
chronos/preprocess/      # Normalization and time-bin processing
chronos/init/            # Coordinate augmentation and dataset validation
chronos/common/          # Frozen array loader and shared contracts
docs/METHODOLOGY.md      # Detailed dataset-construction report
docs/SCHEMA.md           # Final file specifications
tools/inventory.py       # Local dataset and cache inventory
```

## 🔗 Using the dataset

The accompanying **Chronos Experiments** repository consumes the versioned archive under `data/singapore/processed/full/`. Keep its manifest and integrity hashes unchanged when reproducing the original paper runs.

**Source acknowledgements:** © OpenStreetMap contributors; Singapore Land Transport Authority DataMall. [Dataset construction](docs/METHODOLOGY.md) · [Schema](docs/SCHEMA.md) · [Data notices](NOTICE.md).
