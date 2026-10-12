"""Load frozen Chronos-SG arrays into one dataset object."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ChronosSGDataset:
    """Keep the frozen Chronos-SG arrays and metadata in one immutable object."""

    processed_dir: Path
    manifest: dict
    travel_time: np.ndarray
    speed: np.ndarray
    edge_index: np.ndarray
    edge_index_node_ids: np.ndarray
    node_ids: np.ndarray
    edge_metadata: pd.DataFrame
    time_buckets: pd.DataFrame
    node_coordinates: np.ndarray = None

    @property
    def num_time_buckets(self) -> int:
        """Return the number of 15-minute temporal buckets in the dataset."""

        return int(self.travel_time.shape[0])

    @property
    def num_edges(self) -> int:
        """Return the number of directed road edges in the dataset."""

        return int(self.travel_time.shape[1])

    @property
    def num_nodes(self) -> int:
        """Return the number of OSM nodes represented by the road graph."""

        return int(len(self.node_ids))


def load_dataset(processed_dir: Path, mmap_mode: str = "r") -> ChronosSGDataset:
    """Load the processed Chronos-SG arrays produced by the dataset project."""

    processed_dir = Path(processed_dir)
    with (processed_dir / "dataset_manifest.json").open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    files = manifest["files"]
    node_coordinates = None
    coord_file = files.get("node_coordinates") or manifest.get("node_coordinates")
    if coord_file:
        node_coordinates = np.load(processed_dir / coord_file, mmap_mode=mmap_mode)
    try:
        edge_metadata = pd.read_parquet(processed_dir / files["edge_metadata"])
    except ImportError as exc:
        raise ImportError(
            "Chronos preprocessing requires a parquet engine for edge_metadata. "
            "Install the preprocessing dependencies, for example: pip install -r requirements-preprocessing.txt"
        ) from exc

    return ChronosSGDataset(
        processed_dir=processed_dir,
        manifest=manifest,
        travel_time=np.load(processed_dir / files["travel_time"], mmap_mode=mmap_mode),
        speed=np.load(processed_dir / files["speed"], mmap_mode=mmap_mode),
        edge_index=np.load(processed_dir / files["edge_index"], mmap_mode=mmap_mode),
        edge_index_node_ids=np.load(processed_dir / files["edge_index_node_ids"], mmap_mode=mmap_mode),
        node_ids=np.load(processed_dir / files["node_ids"], mmap_mode=mmap_mode),
        edge_metadata=edge_metadata,
        time_buckets=pd.read_csv(processed_dir / files["time_buckets"], parse_dates=["time_bucket"]),
        node_coordinates=node_coordinates,
    )
