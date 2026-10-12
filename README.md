# 🗺️ Chronos-SG-2026 Dataset

**A time-dependent directed road-network dataset for secure multi-stop navigation.**

[![Python](https://img.shields.io/badge/Python-3.11-blue)](https://www.python.org/)
![Region](https://img.shields.io/badge/Region-Singapore-1f6feb)
![Resolution](https://img.shields.io/badge/Temporal%20Resolution-15%20min-brightgreen)
[![Dataset Release](https://img.shields.io/badge/Dataset-v1.0.0-orange)](https://github.com/NavTest1/Chronos/releases/tag/v1.0.0)

*Chronos-SG-2026* is a time-dependent road-network dataset developed for the **Chronos** secure semi-constrained navigation study. This repository provides the dataset processing, validation, and packaging code, together with documentation for preparing compatible road-network data.

## 📌 Dataset at a Glance

| Property | Specification |
| :-- | :-- |
| Region | Singapore |
| Road network | Directed driving graph |
| Road topology source | OpenStreetMap (OSM) |
| Traffic information source | LTA DataMall Traffic Speed Bands |
| Nodes | **24,206** |
| Directed edges | **46,070** |
| Time intervals | **96** per day |
| Temporal resolution | **15 minutes** |
| Projected coordinate system | **EPSG:3414** |
| Processed format | Directed graph arrays and time-dependent edge weights |

The specifications above describe the frozen dataset used in the Chronos study.

## 📦 Dataset Download

The dataset is available as a versioned GitHub Release:

**➡️ [Download Chronos-SG-2026 v1.0.0](https://github.com/NavTest1/Chronos/releases/tag/v1.0.0)**

| Release asset | Description |
| :-- | :-- |
| **`data.zip`** | Chronos-SG-2026 dataset archive |

Download `data.zip` from the release page. On Windows, extract the archive with File Explorer, or use PowerShell:

```powershell
Expand-Archive -LiteralPath .\data.zip -DestinationPath . -Force
```

The processing and experiment tools expect the dataset under `data/singapore/`. In particular, the processed road data should be accessible at:

```text
data/singapore/processed/full/
```

If the downloaded archive contains an additional enclosing folder, move its `data/singapore/` directory to the repository root before running the commands below.

## ⚙️ 1. Installation

**Recommended environment: Windows PowerShell and Python 3.11.**

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run subsequent commands from the repository root.

## 🧭 2. Dataset Construction

The construction process integrates a directed road graph with temporally indexed traffic attributes:

```text
OSM directed road graph         LTA traffic observations
           |                              |
           +--------- road-link alignment+
                          |
               15-minute edge-time table
                          |
              Structural and temporal checks
                          |
               Directed graph / edge arrays
                          |
                Coordinate projection
                          |
               Processed dataset + manifest
```

The provided processor begins with an **aligned edge-time table** and its corresponding road graph. Methodology details are available in [Dataset Methodology](docs/METHODOLOGY.md), and the processed file structure is described in [Dataset Schema](docs/SCHEMA.md).

### Input Files

```text
data/singapore/
├── raw/
│   └── singapore_drive.graphml
├── traffic/
│   └── edge_speed_timeseries_15min.parquet
└── processed/
    └── full/
```

The aligned edge-time input supports Parquet or CSV and uses the following fields:

| Field | Description |
| :-- | :-- |
| `time_bucket` | 15-minute temporal interval |
| `osm_edge_id` | Directed road-edge identifier |
| `osm_u`, `osm_v` | Start and end node identifiers |
| `speed_kmh` | Observed or aligned speed in km/h |
| `travel_time_sec` | Edge traversal time in seconds |

### Build the Processed Dataset

```powershell
python -m dataset.build from-edge-time `
  --input data/singapore/traffic/edge_speed_timeseries_15min.parquet `
  --graphml data/singapore/raw/singapore_drive.graphml `
  --output data/singapore/processed/full `
  --expect-nodes 24206 --expect-edges 46070 --expect-buckets 96
```

The build command writes validated processed files and prints a summary containing the graph dimensions and dataset fingerprint. If you have already extracted the released processed dataset, you can skip rebuilding it.

## ✅ 3. Dataset Validation

Validate the existing processed dataset:

```powershell
python -m dataset.build validate --processed data/singapore/processed/full
```

For a compact test of the data-processing interface, generate and validate a small deterministic example:

```powershell
python -m dataset.build micro --run-dir runs/micro-data
python -m dataset.build validate --processed runs/micro-data/processed
```

The example contains 4 nodes, 6 directed edges, and 4 time bins.

## 🛠️ 4. Dataset Packaging

To prepare a new versioned archive from a validated processed dataset:

```powershell
python -m dataset.package_release `
  --processed-dir data/singapore/processed/full `
  --output ..\Chronos-SG-2026-processed.zip --verify
```

This tool generates a ZIP archive, a manifest, and a SHA-256 checksum for the generated file. The current public release is available from the [v1.0.0 download page](https://github.com/NavTest1/Chronos/releases/tag/v1.0.0).

## 📁 Repository Structure

```text
Chronos-SG-2026-Dataset/
├── chronos/             # Core data processing and validation modules
├── dataset/             # Build and packaging entry points
├── docs/                # Methodology and dataset schema
├── tools/               # Dataset inventory utilities
├── README.md
└── requirements.txt
```

## 🔗 Use in Chronos Experiments

The **Chronos** experiment workflow consumes the processed data in `data/singapore/processed/full/`. Use the released dataset version consistently when reproducing the paper experiments.

**Data sources:** [OpenStreetMap](https://www.openstreetmap.org/) contributors and the [Land Transport Authority of Singapore (LTA DataMall)](https://datamall.lta.gov.sg/). Data usage and redistribution remain subject to the respective providers' terms.

---

**Documentation:** [Methodology](docs/METHODOLOGY.md) · [Dataset Schema](docs/SCHEMA.md) · [Dataset Release v1.0.0](https://github.com/NavTest1/Chronos/releases/tag/v1.0.0)
