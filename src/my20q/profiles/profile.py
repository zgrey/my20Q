"""Patient-profile model.

The profile is the sanctioned channel for patient-level context reaching
the LLM. It is also the privacy pivot: a *real* (non-synthetic) profile
forces local-only inference and turns recording on (see
`docs/design/beta-retool.md` §5).

This is the minimal beta loader — enough to drive the privacy invariant
and seed reasoning. The full caregiver-configured profile (medications,
hobbies, family names, frequent-need shortcuts) is Phase 5 scope.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PatientProfile(BaseModel):
    """A single patient (or, for development, a synthetic persona)."""

    id: str
    display_name: str
    synthetic: bool = Field(
        default=False,
        description=(
            "True marks a development persona — never a real patient. "
            "Synthetic profiles permit the cloud backend and disable recording; "
            "real profiles force local-only inference and enable recording."
        ),
    )
    context: str | None = Field(
        default=None,
        description="Caregiver-written basic context, threaded into reasoning as a prior.",
    )
    caregivers: list[str] = Field(
        default_factory=list,
        description=(
            "Names of the patient's caregivers. Drives the care-first "
            "ask-order prior: a need involving a caregiver most often asks "
            "them to do a care task, so that direction is tested first — "
            "order only, never assumed."
        ),
    )


def is_real_patient(profile: PatientProfile | None) -> bool:
    """The privacy pivot: True iff a loaded profile describes a real patient.

    No profile loaded counts as *not* a real patient — development /
    synthetic territory.
    """
    return profile is not None and not profile.synthetic
