"""Patient profiles — patient-level context and the privacy pivot."""

from __future__ import annotations

from my20q.profiles.loader import load_profile
from my20q.profiles.profile import PatientProfile, is_real_patient

__all__ = ["PatientProfile", "is_real_patient", "load_profile"]
