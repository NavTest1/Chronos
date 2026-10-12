"""Define public bounds and default paths shared by every Chronos stage."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChronosDefaults:
    """Store public bounds used by the Chronos system pipeline."""

    time_bucket_minutes: int = 15
    partition_count: int = 8
    anchors_per_kind: int = 2
    nearest_anchors: int = 4
    candidate_paths: int = 2
    max_stops: int = 8
    max_partial_orders: int = 5
    max_time_windows: int = 5
    workspace_transition_slots: int = 64
    route_tree_slots: int = 64
    complete_slots: int = 16
    route_ref_slots: int = 16
    eta0: float = 0.10
    rho1: float = 0.50
    rho2: float = 0.50


@dataclass(frozen=True)
class ChronosPaths:
    """Resolve dataset and generated-index locations."""

    project_root: Path = Path(__file__).resolve().parents[2]
    dataset_project: Path = project_root
    processed_dir: Path = project_root / "processed"
    offline_index_dir: Path = project_root / "offline_index"
    protected_index_dir: Path = project_root / "protected_index"
    do_context_file: Path = project_root / "do_context" / "do_context.bin"


DEFAULTS = ChronosDefaults()
PATHS = ChronosPaths()
