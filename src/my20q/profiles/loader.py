"""YAML loader for patient profiles."""

from __future__ import annotations

from pathlib import Path

import yaml

from my20q.profiles.profile import PatientProfile


def load_profile(path: Path | None) -> PatientProfile | None:
    """Load a patient profile from a YAML file.

    Returns None when `path` is None (no profile configured) — development
    territory. Raises if a path is given but missing or malformed; a
    profile that silently fails to load could wrongly drop the privacy
    invariant, so loading fails loud.
    """
    if path is None:
        return None
    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"Patient profile not found: {src}")
    with src.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"Patient profile at {src} must be a YAML mapping at the root")
    return PatientProfile.model_validate(raw)
