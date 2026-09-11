"""Runtime configuration, sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Mode = Literal["training", "operational"]
BackendChoice = Literal["ollama", "anthropic"]
TTSEngineChoice = Literal["piper", "kokoro"]


@dataclass(frozen=True)
class ReasoningTuning:
    """Tunable knobs for the round state machine — all env-overridable.

    These shape *behavior* (how long to question, when to synthesize, when to
    rephrase vs. restart, how often to explore) WITHOUT code edits — the serve
    script can export the ``MY20Q_*`` vars below. ``agent/dialogue.py`` reads
    these off the active ``Round.tuning``.
    """

    # DELETED 2026-09-11: min_yes_for_synthesis / new_yes_for_resynthesis /
    # rephrase_limit / synth_attempts_before_restart. They were retired in
    # 06-11 when the living proposal banner replaced engine-initiated synthesis,
    # and kept with their env vars "until the banner survives a live trial".
    # It has — four accepted rounds on 09-09 plus the 09-10 and 09-11 trials —
    # so the condition for removing them is met. Until now MY20Q_MIN_YES,
    # MY20Q_NEW_YES, MY20Q_REPHRASE_LIMIT and MY20Q_SYNTH_ATTEMPTS were parsed,
    # clamped, threaded through the serve script and documented to the operator
    # as live tuning, while changing nothing at all.

    #: Exploration DECAYS as yeses accrue toward synthesis. The next question is
    #: exploratory (profile dropped, free to probe a brand-new on-topic value) with
    #: probability ``explore_decay ** (yeses + 1)`` — high early, low as the round
    #: homes in. The yes-count resets after each synthesis attempt and after a
    #: restart, so exploration re-opens. Base in [0, 1]; 2/3 ≈ 0.67 initially.
    explore_decay: float = 2 / 3
    #: MORE than this many CONSECUTIVE "no" answers triggers the restart recovery
    #: (the working context is wrong — dump it). See ``Round._consec_no_streak``.
    soft_reset_no_streak: int = 10
    #: Restart when this many answered queries pass with ZERO progress (no pair
    #: newly reaching a confirmed score, no synthesis attempt) — sparse kindas
    #: can otherwise shield a dead-end round from the no-streak trigger. 0
    #: disables. See ``Round._stalled``.
    stall_window: int = 8
    #: A facet category counts as DETERMINED once its leading contender has at
    #: least this many consensus points (and a clear margin — below). When every
    #: core category is determined, synthesis can fire early with placeholders
    #: for the rest.
    facet_ready_points: float = 2.0
    #: Top-two contenders of a category within this margin are TIED — the
    #: controller schedules a splitting question; a leader needs at least this
    #: margin over the runner-up to count as determined.
    facet_split_margin: float = 1.0
    #: A slot RETIRES from probe/drill/pin once its leader DOMINATES: leader
    #: >= retire_ready_x * facet_ready_points AND runner-up <= half the
    #: leader. A dominance RATIO, not a margin — margins grow with round
    #: length, and a fixed margin would retire a vague-but-leading value at
    #: exactly the moment it needs drilling (the 06-11 round's what-slot).
    #: Retired slots stay split-eligible (a genuine re-tie reopens them) and
    #: still earn credit; restarts rebuild the board and can un-retire.
    retire_ready_x: float = 3.0

    @classmethod
    def from_env(cls) -> ReasoningTuning:
        def _int(name: str, default: int, *, minimum: int) -> int:
            raw = os.environ.get(name, "").strip()
            return max(minimum, int(raw)) if raw else default

        def _float(name: str, default: float, *, lo: float, hi: float) -> float:
            raw = os.environ.get(name, "").strip()
            return min(hi, max(lo, float(raw))) if raw else default

        return cls(
            explore_decay=_float("MY20Q_EXPLORE_DECAY", 2 / 3, lo=0.0, hi=1.0),
            soft_reset_no_streak=_int("MY20Q_SOFT_RESET_NOS", 10, minimum=1),
            stall_window=_int("MY20Q_STALL_WINDOW", 8, minimum=0),
            facet_ready_points=_float("MY20Q_FACET_READY", 2.0, lo=0.5, hi=1e9),
            facet_split_margin=_float("MY20Q_SPLIT_MARGIN", 1.0, lo=0.0, hi=1e9),
            retire_ready_x=_float("MY20Q_RETIRE_X", 3.0, lo=1.0, hi=1e9),
        )


@dataclass(frozen=True)
class Config:
    """Process-wide settings. See `from_env` for the environment variables.

    The privacy invariant (recording => real patient => local LLM) is
    enforced at backend-selection time, not here — this object only
    carries the raw settings.
    """

    ollama_base_url: str
    ollama_model: str
    ollama_timeout_s: float
    anthropic_api_key: str | None
    anthropic_model: str
    llm_backend: BackendChoice
    topics_path: Path
    profile_path: Path | None
    llm_enabled: bool
    max_queries: int
    mode: Mode
    recording_dir: Path
    recording_threshold_bytes: int
    #: Dev capture for SYNTHETIC personas only (MY20Q_DEV_CAPTURE=1). It exists
    #: so a development trial leaves a round record to audit, which the patient
    #: dataset cannot do because it is off by design for a synthetic run.
    #:
    #: It does NOT weaken the privacy invariant, and the distinction is the
    #: whole point: the PATIENT DATASET still implies a real patient and
    #: therefore a local LLM. This is a different artifact with the mirror-image
    #: guard — dev capture implies SYNTHETIC — so the two can never both be
    #: writing, and no path exists by which real patient content reaches this
    #: directory. `create_app` refuses to enable it when a real profile is
    #: loaded rather than silently preferring one channel over the other.
    dev_capture: bool
    dev_capture_dir: Path
    # Text-to-speech (local-only — never a cloud voice). The engine is
    # selectable: `piper` (fast, flat) or `kokoro` (more natural). Models are
    # installed on the host; until then the cockpit reports audio as unavailable
    # and stays silent. See tts/.
    tts_enabled: bool
    tts_engine: TTSEngineChoice
    piper_bin: str
    piper_model: Path | None
    piper_timeout_s: float
    # Kokoro (kokoro-onnx) — used when tts_engine == "kokoro". Needs the model
    # (.onnx) and voices (.bin) files downloaded once; see tts/kokoro_tts.py.
    kokoro_model: Path | None
    kokoro_voices: Path | None
    kokoro_voice: str
    kokoro_speed: float
    kokoro_lang: str
    # Round state-machine behavior knobs (env-overridable; see ReasoningTuning).
    reasoning: ReasoningTuning

    @classmethod
    def from_env(cls) -> Config:
        default_topics = Path(__file__).parent / "topics" / "data" / "topics.yaml"

        mode = os.environ.get("MY20Q_MODE", "training").strip().lower()
        if mode not in ("training", "operational"):
            raise ValueError(f"MY20Q_MODE must be 'training' or 'operational', got {mode!r}")

        backend = os.environ.get("MY20Q_BACKEND", "ollama").strip().lower()
        if backend not in ("ollama", "anthropic"):
            raise ValueError(f"MY20Q_BACKEND must be 'ollama' or 'anthropic', got {backend!r}")

        profile_env = os.environ.get("MY20Q_PROFILE", "").strip()
        piper_model_env = os.environ.get("MY20Q_PIPER_MODEL", "").strip()

        # An empty value (e.g. the serve script exporting MY20Q_TTS_ENGINE='')
        # means "unset" — fall back to the default rather than rejecting it.
        tts_engine = (os.environ.get("MY20Q_TTS_ENGINE") or "piper").strip().lower()
        if tts_engine not in ("piper", "kokoro"):
            raise ValueError(
                f"MY20Q_TTS_ENGINE must be 'piper' or 'kokoro', got {tts_engine!r}"
            )
        kokoro_model_env = os.environ.get("MY20Q_KOKORO_MODEL", "").strip()
        kokoro_voices_env = os.environ.get("MY20Q_KOKORO_VOICES", "").strip()

        return cls(
            ollama_base_url=os.environ.get("MY20Q_OLLAMA_URL", "http://localhost:11434"),
            # gemma4:e4b (a thinking model) — drills down a warm trail far better
            # than gemma3:12b, which wandered laterally. Two-phase + slower, but
            # the quality win is worth it for the questioning. Swap via env.
            ollama_model=os.environ.get("MY20Q_OLLAMA_MODEL", "gemma4:e4b"),
            ollama_timeout_s=float(os.environ.get("MY20Q_OLLAMA_TIMEOUT", "120")),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            anthropic_model=os.environ.get("MY20Q_ANTHROPIC_MODEL", "claude-opus-4-7"),
            llm_backend=backend,  # type: ignore[arg-type]
            topics_path=Path(os.environ.get("MY20Q_TOPICS", str(default_topics))),
            profile_path=Path(profile_env) if profile_env else None,
            llm_enabled=os.environ.get("MY20Q_LLM", "1") != "0",
            # 0 = unlimited (the default): synthesis is readiness-driven, not
            # capped by a question count. Set a positive value only as a hard
            # safety ceiling that ends the round without forcing an utterance.
            max_queries=int(os.environ.get("MY20Q_MAX_QUERIES", "0")),
            mode=mode,  # type: ignore[arg-type]
            recording_dir=Path(os.environ.get("MY20Q_DATA_DIR", "patient_data")),
            dev_capture=os.environ.get("MY20Q_DEV_CAPTURE", "0") == "1",
            dev_capture_dir=Path(
                os.environ.get("MY20Q_DEV_CAPTURE_DIR", "dev_recordings")
            ),
            recording_threshold_bytes=(
                int(os.environ.get("MY20Q_RECORDING_THRESHOLD_MB", "25")) * 1024 * 1024
            ),
            tts_enabled=os.environ.get("MY20Q_TTS", "1") != "0",
            tts_engine=tts_engine,  # type: ignore[arg-type]
            piper_bin=os.environ.get("MY20Q_PIPER_BIN", "piper"),
            piper_model=Path(piper_model_env) if piper_model_env else None,
            piper_timeout_s=float(os.environ.get("MY20Q_PIPER_TIMEOUT", "20")),
            kokoro_model=Path(kokoro_model_env) if kokoro_model_env else None,
            kokoro_voices=Path(kokoro_voices_env) if kokoro_voices_env else None,
            kokoro_voice=os.environ.get("MY20Q_KOKORO_VOICE", "af_heart"),
            kokoro_speed=float(os.environ.get("MY20Q_KOKORO_SPEED", "1.0")),
            kokoro_lang=os.environ.get("MY20Q_KOKORO_LANG", "en-us"),
            reasoning=ReasoningTuning.from_env(),
        )
