"""Compare candidate LLMs on my20Q's real task: driving a round to converge.

Unlike the (retired) Phase-1 ``bench_models.py``, this exercises the actual
``Round``/``Reasoner`` loop. For each candidate model and each scenario:

  * the candidate drives the round (proposes queries / a synthesis),
  * a fixed *simulator* model role-plays the patient — it knows the scenario's
    hidden need and answers each yes/no query (and confirms/rejects the
    proposed utterance) accordingly,

so the only thing that varies between runs is the reasoner. We then report,
per model: how often it converged on the right need, how many queries it took,
how often it degraded to fallback (= failed to produce usable JSON), auditor
re-asks, and average reasoner latency per query — plus the full transcripts,
grouped by scenario, for eyeballing the reasoning quality side by side.

The simulator is held constant across all candidates, so its own quirks bias
every model equally; its answers are logged so you can audit them. It defaults
to local ``gemma4:e4b`` so the bench is reproducible offline; pass
``--simulator claude-haiku-4-5-20251001`` to use Anthropic instead (permitted
here since these are synthetic personas — real_patient=false — and it needs
ANTHROPIC_API_KEY). Candidates run at temperature 0 with think=False (so
gemma4's hidden reasoning tokens don't exhaust the budget before the JSON
answer).

W2-G (the noise bench) adds three things to the model sweep above, all behind
flags so the default run is unchanged:

  * ``--noise`` — ε-noise on the simulated answerer: yes↔no flipped with
    probability ε, "kinda"/"not sure" untouched. The caregiver's final accept
    stays a CLEAN oracle (owner decision), so "converged" keeps meaning
    converged *to the right need despite noise* and a delta stays attributable
    to the engine rather than to the harness.
  * per-round metrics computed from ``build_round_record`` — the same artifact
    ``dump_recording.py`` reads, deliberately NOT from engine internals, so
    everything reported here is provably present in a real recording. This is
    what makes the §1 autopsy table a script output instead of hand-work; it
    needs the W2-O instrumentation to be there.
  * ``--compare`` — diff two result files, i.e. two engine revisions.

Prerequisites:
  1. ``ollama serve`` running (default http://localhost:11434).
  2. The candidate models are pulled (`ollama pull`). Models that aren't pulled
     are skipped with a note — this script never pulls for you.
  3. For an Anthropic simulator only: ANTHROPIC_API_KEY set and the ``trials``
     extra installed (pip install -e '.[trials]').

Usage:
  python scripts/bench_reasoning.py                       # local simulator
  python scripts/bench_reasoning.py --models gemma3:12b gemma4:e4b
  python scripts/bench_reasoning.py --simulator claude-haiku-4-5-20251001
  python scripts/bench_reasoning.py --json out.json --no-transcripts

  # measure an engine change (W2-G):
  python scripts/bench_reasoning.py --models gemma4:e4b --json before.json
  #   ... change the engine ...
  python scripts/bench_reasoning.py --models gemma4:e4b --json after.json
  python scripts/bench_reasoning.py --compare before.json after.json

  # how well does it hold up under a noisy answerer?
  python scripts/bench_reasoning.py --models gemma4:e4b --noise 0 0.1 0.2
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import random
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rich.console import Console
from rich.table import Table

from my20q.agent.dialogue import Answer, Round
from my20q.llm.base import LLMBackend, LLMUnavailable
from my20q.llm.ollama_client import OllamaBackend
from my20q.recording import build_round_record
from my20q.topics import Topic, find_topic, load_topics

# Transcripts echo model output and a few unicode marks (✓/✗/“”). The legacy
# Windows console encodes to cp1252 and would crash on those, so force UTF-8 on
# the streams and skip Rich's legacy renderer.
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
console = Console(legacy_windows=False)

# Full sweep + small-model baselines. Override with --models.
DEFAULT_MODELS: list[str] = [
    "gemma4:e4b",  # current production default (thinking; drills best)
    "gemma3:12b",
    "gemma4:26b",  # spills past 16 GB VRAM — slow, but tests "bigger reasons better"
    "gemma3:4b",  # baseline
    "llama3.2:3b",  # baseline
]

#: The patient simulator — held constant across all candidates. A name starting
#: with "claude" uses the Anthropic backend (reliable, zero VRAM cost, permitted
#: here since these are synthetic personas); anything else is a local Ollama
#: model. Override with --simulator.
#:
#: Local by default (owner decision, 09-08): ANTHROPIC_API_KEY is not set on the
#: dev machine, and a local default keeps the bench reproducible offline. The
#: simulator only has to answer yes/no about a need it is told outright, which
#: is well within a small local model.
DEFAULT_SIMULATOR = "gemma4:e4b"


def _make_simulator(name: str) -> LLMBackend:
    """Build the patient simulator backend from its model name."""
    if name.startswith("claude"):
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit(
                f"ANTHROPIC_API_KEY is not set — required for the Anthropic "
                f"simulator {name!r}. Set it (setx ANTHROPIC_API_KEY ...) or "
                f"pass a local --simulator (e.g. gemma3:12b)."
            )
        from my20q.llm.anthropic_client import AnthropicBackend

        return AnthropicBackend(api_key=key, model=name)
    # think=False is load-bearing, not tidiness. The simulator's calls are
    # tiny (max_tokens 4-8, one word expected), and gemma4 is a THINKING model:
    # with thinking on, the budget is spent before any content is emitted and
    # OllamaBackend salvages the chain-of-thought instead ("Thinking Process:
    # 1. **Analyze the Goal:** ..."). `_simulate_confirm` checks
    # `startswith("y")`, so it was returning False for EVERY draft — the bench's
    # `converged` column was structurally zero rather than merely strict, in
    # every run to date. Found by printing the raw response.
    return OllamaBackend(model=name, timeout_s=60.0, temperature=0.0, think=False)


@dataclass
class Scenario:
    """A hidden need the simulated patient is trying to express."""

    name: str
    topic_id: str
    need: str  # first-person need, known only to the simulator


SCENARIOS: list[Scenario] = [
    Scenario("thirsty", "physical_health", "I am thirsty and want a glass of water."),
    Scenario("foot-pain", "physical_health", "My left foot hurts."),
    Scenario("cold", "general", "I am too cold and want a blanket."),
    Scenario("lonely", "mental_health", "I feel lonely and want someone to sit with me."),
    Scenario("anxious-noise", "mental_health", "I feel anxious and want the room quieter."),
    Scenario("call-daughter", "my_people", "I want to phone my daughter."),
    # The two June-2026 live-trial targets — canonical regressions for the
    # direction layer (they-do-for-me requests; gemma3 failed the second live).
    Scenario(
        "move-picture",
        "my_people",
        "I need Zach to come over and help me move a large picture in the house.",
    ),
    Scenario("rob-kitchen", "my_people", "I need Rob to clean the kitchen."),
    # The 09-01 regression, as a SYNTHETIC analogue (owner decision): the round
    # it stands for was a real-patient session, and its clinical detail must
    # never be committed — the privacy invariant, and `patient_data/` is
    # gitignored precisely so. What regressed there was the MECHANISM, not the
    # words: a laterality-bearing body part folded onto its coarse parent, so
    # every drill-down answer landed on "right side" and no refinement link
    # ever formed (see convergence-plan W2-K). Any need with the same shape —
    # a side, then a limb, then a part of that limb — exercises it identically.
    Scenario(
        "leg-laterality",
        "physical_health",
        "My right thigh is tingling — the right leg, not the left, and not my calf.",
    ),
]


# ------------------------------------------------------------- simulator

_SIM_SYSTEM = """\
You role-play a person with aphasia who is trying to communicate ONE specific
need to a caregiver. Answer the caregiver's yes/no questions truthfully, about
THIS need only:

  YOUR NEED: {need}

Reply with EXACTLY ONE lowercase word, no punctuation:
- "yes"      the question is TRUE of your need — judge by MEANING, so broader
             or synonym phrasings still count (e.g. "pain", "hurts", "sore",
             "aching", "discomfort" all match a need that hurts; "drink",
             "thirsty", "water" all match wanting water).
- "no"       the question is FALSE of your need — INCLUDING a question about
             something else entirely. You know what you need, so if it is not
             that, say no.
- "kinda"    partly true / on the right track but not exact.
- "not_sure" you genuinely cannot tell — the question asks for a detail you do
             not know about your own need. This should be RARE.

You know your need exactly, so answer decisively. Do NOT use "not_sure" merely
because a question is about a different subject — that is a "no". If a question
points at the right thing even loosely, answer "yes". Never explain — one word
only.

EXAMPLES (for an example need "my left foot hurts"):
  "Is something hurting you?"            -> yes
  "Is the pain in your foot?"            -> yes
  "Is it a general discomfort or pain?"  -> yes
  "Are you hungry?"                      -> no
  "Are you feeling any tingling?"        -> no
  "Is it your hand?"                     -> no
  "Did it start before lunchtime?"       -> not_sure
"""

# This stands in for the caregiver pressing ✓, so it must judge by the standard
# a caregiver actually applies: "said aloud, would this get me what I need?"
# The earlier wording — "does that sentence correctly capture your need?" —
# invited pedantry, and it rejected "I need help getting a blanket over me
# right now" for the need "I am too cold and want a blanket". That is a draft a
# person would happily have spoken for them; the real caregiver accepted three
# drafts of exactly that character in the 09-09 trials. An oracle stricter than
# the human it represents caps `converged` below what the engine deserves,
# which is why reached_ready had to become the honest metric.
_SIM_CONFIRM = """\
Someone is about to say this out loud on your behalf:
  "{utterance}"

What you actually need is: {need}

Would you be content to have that said for you? Judge it the way a person
would, not word by word:
- YES if saying it would get you what you need — even if it is worded
  differently, leaves out a detail, or adds a reasonable one.
- NO if it would send someone after the wrong thing, or gets a specific detail
  wrong (the wrong person, the wrong side of the body, the wrong item).
- NO if it is too vague for anyone to act on. It must name the thing you
  actually want. A sentence that leaves the main thing as "something" or
  "someone", or that just names a feeling or a symptom, helps nobody:
  "I need something for someone", "I need pain", "I need overwhelmed" are all
  NO, however true they sound.

Ask yourself: if a helper heard only this sentence, would they know what to do?
If not, answer no.

Reply EXACTLY one word: "yes" or "no". One word only.
"""

_ANSWER_WORDS = {a.value: a for a in Answer}


async def _simulate_answer(sim: LLMBackend, need: str, question: str) -> Answer:
    messages = [
        {"role": "system", "content": _SIM_SYSTEM.format(need=need)},
        {"role": "user", "content": f'The caregiver asks: "{question}"'},
    ]
    try:
        raw = await sim.chat(messages, max_tokens=8)
    except LLMUnavailable:
        return Answer.NOT_SURE
    token = raw.strip().lower().strip(".!\"'").split()[0] if raw.strip() else ""
    token = token.replace("not sure", "not_sure")
    return _ANSWER_WORDS.get(token, Answer.NOT_SURE)


async def _simulate_confirm(sim: LLMBackend, need: str, utterance: str) -> bool:
    """Does this draft actually capture the hidden need?

    Deliberately NEVER noised, even in a noise sweep (owner decision): this is
    the run's ground-truth oracle. Keeping it clean is what lets "converged"
    mean *converged to the right need despite noisy answers* — noise the
    oracle too and a convergence drop stops being attributable to the engine.
    """
    messages = [
        {"role": "system", "content": "Answer with exactly one word: yes or no."},
        {"role": "user", "content": _SIM_CONFIRM.format(utterance=utterance, need=need)},
    ]
    try:
        raw = await sim.chat(messages, max_tokens=8)
    except LLMUnavailable:
        return False
    return raw.strip().lower().startswith("y")


def apply_noise(answer: Answer, epsilon: float, rng: random.Random) -> Answer:
    """ε-noise on one answer: flip yes↔no with probability ε (W2-G).

    "kinda" and "not sure" pass through untouched — they are hedges, and the
    Rényi–Ulam noise model this comes from is about a decisive answer being
    *wrong*, not about a hedge becoming decisive. ε=0 is the identity, so a
    noiseless run takes exactly the same path as before this existed.
    """
    if epsilon <= 0.0 or answer not in (Answer.YES, Answer.NO):
        return answer
    if rng.random() >= epsilon:
        return answer
    return Answer.NO if answer is Answer.YES else Answer.YES


# -------------------------------------------------------------- metrics


def round_metrics(record: dict) -> dict:
    """The §1 autopsy metrics for one round, read from its RECORD (W2-G).

    Deliberately takes a ``build_round_record`` dict rather than a live
    ``Round``: it is the same artifact ``dump_recording.py`` reads and the
    recorder writes, so every number here is provably obtainable from a real
    recorded session. Reading engine internals instead would let the bench
    report things a recording cannot, which is exactly the gap W2-O closed.

    Fields that only exist on W2-O-era records (banner / timing / rejections /
    restart positions) come back ``None`` on an older one rather than raising,
    so this can also be pointed at an archived recording.
    """
    entries = record.get("queries", [])
    queries = [e for e in entries if e.get("kind") == "query"]
    answered = [e for e in queries if e.get("answer")]
    yeses = [e for e in queries if e.get("answer") == "yes"]
    # An "informative" yes moved the board; a farming yes re-confirmed what was
    # already established. The ratio is the round's signal-per-yes.
    informative = [e for e in yeses if e.get("informative")]
    restarts = (record.get("board") or {}).get("restarts") or []

    # The banner metric: the query at which the draft first became offerable,
    # and how many were asked past that point. This is the honest form of §1's
    # "wasted query estimate" — it requires the W2-O banner snapshot.
    ready_at = next(
        (
            i
            for i, e in enumerate(queries, start=1)
            if (e.get("banner") or {}).get("ready")
        ),
        None,
    )

    timings = [e["timing"] for e in queries if e.get("timing")]
    latency = {}
    if timings:
        totals = sorted(t.get("total_ms", 0.0) for t in timings)
        latency = {
            "mean_ms": round(statistics.mean(totals), 1),
            "p50_ms": round(statistics.median(totals), 1),
            "max_ms": round(totals[-1], 1),
        }

    return {
        "converged": record.get("outcome") == "synthesized",
        "outcome": record.get("outcome"),
        "queries": len(queries),
        "job_b": record.get("job_b"),
        "answers": dict(Counter(e.get("answer") for e in answered)),
        "focus": dict(Counter(e["focus"] for e in queries if e.get("focus"))),
        "informative_yes": len(informative),
        "farming_yes": len(yeses) - len(informative),
        "restarts": len(restarts),
        "restart_positions": [
            r["after_query"] for r in restarts if r.get("after_query") is not None
        ],
        "diagnostics": sum(1 for e in entries if e.get("kind") == "diagnostic"),
        "verifies": sum(1 for e in queries if e.get("verify")),
        "ready_at_query": ready_at,
        "queries_after_ready": None if ready_at is None else len(queries) - ready_at,
        # Replaces the old `reasks` counter, which matched rationale prefixes
        # ("(re-asked", "(auditor") that the v3 engine stopped emitting — it
        # had been silently reporting 0 on every run.
        "gate_rejections": sum(len(e.get("rejections") or []) for e in queries),
        "questions_re_asked": sum(1 for e in queries if e.get("rejections")),
        "latency": latency,
        "seed_ms": record.get("seed_ms"),
        "left_unanswered": bool(record.get("pending_question")),
    }


def _mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 2) if values else None


def aggregate_metrics(rounds: list[dict]) -> dict:
    """Mean the per-round metrics over a run — the compare mode's unit.

    ``queries_to_converge`` is convergence-conditional and averages over the
    CONVERGED rounds only: a round that hit the query cap would otherwise
    flatter or punish the mean depending on where the cap sits.

    ``reached_ready`` and ``queries_after_ready`` are deliberately NOT
    conditioned on convergence. Reaching readiness is the engine's own
    milestone — the point where the draft becomes a woven sentence instead of
    the code template — and it is reachable in a round the caregiver never
    accepts. Scoping them to converged rounds made both read "—" for a run
    where the board reached readiness in a third of its rounds, which hid the
    entire effect of the W2-R fix on its first measurement.
    """
    if not rounds:
        return {}
    conv = [m for m in rounds if m.get("converged")]
    got_ready = [m for m in rounds if m.get("ready_at_query") is not None]
    ready = [
        m["queries_after_ready"]
        for m in got_ready
        if m.get("queries_after_ready") is not None
    ]
    yes_total = sum(m["informative_yes"] + m["farming_yes"] for m in rounds)
    lat = [m["latency"]["mean_ms"] for m in rounds if m.get("latency")]
    return {
        "rounds": len(rounds),
        "converged": len(conv),
        "reached_ready": len(got_ready),
        "ready_at_query": _mean([float(m["ready_at_query"]) for m in got_ready]),
        "queries_to_converge": _mean([float(m["queries"]) for m in conv]),
        "queries_after_ready": _mean([float(x) for x in ready]),
        "informative_yes_ratio": (
            round(sum(m["informative_yes"] for m in rounds) / yes_total, 3)
            if yes_total
            else None
        ),
        "restarts": _mean([float(m["restarts"]) for m in rounds]),
        "diagnostics": _mean([float(m["diagnostics"]) for m in rounds]),
        "gate_rejections": _mean([float(m["gate_rejections"]) for m in rounds]),
        "latency_ms": _mean(lat),
        "focus": dict(
            sum((Counter(m["focus"]) for m in rounds), start=Counter())
        ),
    }


# --------------------------------------------------------------- runner


@dataclass
class RoundResult:
    scenario: str
    converged: bool
    outcome: str | None
    queries: int
    degraded: bool  # the candidate fell back to deterministic mode mid-round
    degrade_reason: str  # why it degraded (ReasonerError text), if it did
    final_utterance: str
    #: The §1 autopsy metrics, read back off this round's own record.
    metrics: dict = field(default_factory=dict)
    #: Answers the ε-noise wrapper flipped before the engine saw them.
    flipped: int = 0
    transcript: list[str] = field(default_factory=list)
    reasoner_latencies: list[float] = field(default_factory=list)


@dataclass
class ModelReport:
    model: str
    available: bool
    error: str = ""
    #: The ε-noise level this run was driven at (0 = a clean answerer).
    noise: float = 0.0
    rounds: list[RoundResult] = field(default_factory=list)

    @property
    def latencies(self) -> list[float]:
        return [t for r in self.rounds for t in r.reasoner_latencies]

    @property
    def converged(self) -> int:
        return sum(1 for r in self.rounds if r.converged)

    @property
    def degraded(self) -> int:
        return sum(1 for r in self.rounds if r.degraded)

    @property
    def summary(self) -> dict:
        """The run's aggregated §1 metrics — what --compare diffs."""
        return aggregate_metrics([r.metrics for r in self.rounds if r.metrics])


async def _drive_round(
    *,
    reasoner: OllamaBackend,
    sim: LLMBackend,
    topic: Topic,
    scenario: Scenario,
    max_queries: int,
    noise: float = 0.0,
    rng: random.Random | None = None,
) -> RoundResult:
    rnd = Round(topic, llm=reasoner, max_queries=max_queries)
    transcript: list[str] = []
    latencies: list[float] = []
    diagnostics = 0
    flipped = 0
    rng = rng or random.Random(0)

    async def step(coro):
        t0 = time.perf_counter()
        ev = await coro  # candidate reasoner inference happens here
        latencies.append(time.perf_counter() - t0)
        return ev

    ev = await step(rnd.open())
    # The engine never proposes on its own anymore: the banner carries the
    # evolving draft and the caregiver accepts it. The bench plays caregiver:
    # whenever the draft CHANGES, ask the simulator to confirm it — confirm =
    # accept (round ends), reject = keep questioning.
    #
    # Deliberately not gated on banner["ready"], which is what this did before.
    # `ready` is the engine's own propose-ready cue, and a real caregiver is
    # free to accept before it lights: physical_health gates readiness on
    # what+where, so a NON-LOCALIZED need (thirst) never reaches it — the topic
    # YAML says so outright, and the caregiver is expected to ✗-mute `where` or
    # accept pre-ready. Gating on `ready` made those rounds unconvergeable by
    # construction and quietly understated convergence for the whole class.
    # The confirm oracle is ground truth, so offering earlier is safe: it
    # accepts only a draft that genuinely captures the need. `ready` is still
    # recorded per query and reported as a metric — it is just not the gate.
    last_draft = ""
    # Hard cap on iterations so a pathological loop can't run forever.
    for _ in range(max_queries * 2 + 4):
        if rnd.is_terminal:
            break
        if ev.kind == "query":
            truth = await _simulate_answer(sim, scenario.need, ev.text)
            ans = apply_noise(truth, noise, rng)
            if ans is not truth:
                flipped += 1
            mark = f"{ans.value}  [FLIPPED from {truth.value}]" if ans is not truth else ans.value
            transcript.append(f"Q{ev.query_index}: {ev.text}  → {mark}")
            ev = await step(rnd.answer(ans))
            banner = rnd.banner()
            if banner["state"] == "draft" and banner["text"] != last_draft:
                last_draft = banner["text"]
                ok = await _simulate_confirm(sim, scenario.need, banner["text"])
                flag = "READY" if banner["ready"] else "pre-ready"
                transcript.append(
                    f"DRAFT[{flag}]: “{banner['text']}”  → "
                    f"{'accept ✓' if ok else 'keep going'}"
                )
                if ok:
                    ev = rnd.accept()
        elif ev.kind == "diagnostic":
            # No canned questions anymore — a failed turn surfaces a
            # diagnostic. Retry a few times (mirrors the cockpit button), then
            # give up on the round.
            diagnostics += 1
            transcript.append(f"DIAG: {ev.text}")
            if diagnostics > 3:
                break
            ev = await step(rnd.retry())
        else:
            break

    if ev.kind == "synthesized" or rnd.outcome == "synthesized":
        transcript.append(f"✓ CONFIRMED: “{rnd.final_utterance}”")
    if not rnd.is_terminal:
        # Ran out of iterations with the round still live — finalize it so the
        # record is the same shape a real abandoned round produces (and so the
        # unanswered question is captured).
        rnd.abandon()

    # Metrics come off the ROUND RECORD, not the live object: the bench must
    # only report what a real recorded session carries. See round_metrics().
    record = build_round_record(
        session_id="bench",
        round_id=scenario.name,
        topic_id=topic.id,
        engine=rnd.engine,
        history=rnd.history,
        outcome=rnd.outcome,
        final_utterance=rnd.final_utterance,
        model=reasoner.model,
        board=rnd.board_record(),
        seed_context=rnd.seed_context,
        seed_ms=rnd.seed_ms,
        pending_question=rnd.pending_question,
    )
    return RoundResult(
        scenario=scenario.name,
        converged=rnd.outcome == "synthesized",
        outcome=rnd.outcome,
        queries=rnd.query_count,
        degraded=diagnostics > 0,
        degrade_reason=rnd.degrade_reason,
        final_utterance=rnd.final_utterance,
        metrics=round_metrics(record),
        flipped=flipped,
        transcript=transcript,
        reasoner_latencies=latencies,
    )


async def _model_pulled(backend: OllamaBackend) -> bool:
    import httpx2

    try:
        async with httpx2.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{backend.base_url}/api/tags")
            resp.raise_for_status()
            names = {m.get("name", "") for m in resp.json().get("models", [])}
    except httpx2.HTTPError:
        return False
    return backend.model in names or any(n == backend.model for n in names)


async def _bench_model(
    model: str,
    sim: LLMBackend,
    topics: list[Topic],
    scenarios: list[Scenario],
    max_queries: int,
    temperature: float,
    noise: float = 0.0,
    seed: int = 0,
) -> ModelReport:
    # Generous timeout: gemma4:26b spills past 16 GB VRAM and runs partly on
    # CPU, so a single call can be slow — don't unfairly clip it to fallback.
    # think=False stops gemma4's reasoning tokens from eating num_predict before
    # the JSON answer (harmlessly ignored by non-thinking models like gemma3).
    reasoner = OllamaBackend(
        model=model, timeout_s=240.0, temperature=temperature, think=False
    )
    report = ModelReport(model=model, available=False, noise=noise)
    if not await reasoner.health():
        report.error = "ollama not reachable"
        return report
    if not await _model_pulled(reasoner):
        report.error = f"not pulled (ollama pull {model})"
        return report
    report.available = True

    # Warm-up: load the model into VRAM once so the first scenario's latency
    # (and any cold-load timeout that would unfairly degrade it) is excluded.
    console.print(f"[dim]  warming {model}…[/dim]")
    with contextlib.suppress(LLMUnavailable):
        await reasoner.chat([{"role": "user", "content": "Reply: ready"}], max_tokens=4)

    for i, sc in enumerate(scenarios):
        topic = find_topic(topics, sc.topic_id)
        if topic is None:
            continue
        console.print(f"[dim]  {model} · {sc.name}…[/dim]")
        report.rounds.append(
            await _drive_round(
                reasoner=reasoner,
                sim=sim,
                topic=topic,
                scenario=sc,
                max_queries=max_queries,
                noise=noise,
                # Per-scenario stream, derived from the run seed. Seeded from a
                # STRING (hashed with sha512 by random.seed, so it is stable
                # across processes — a tuple's hash is not, PYTHONHASHSEED
                # randomizes it). The same (seed, scenario) therefore draws the
                # same flips across engine revisions, so a --compare delta is a
                # real delta and not just a different coin sequence.
                rng=random.Random(f"{seed}:{sc.name}:{noise}:{i}"),
            )
        )
    return report


# --------------------------------------------------------------- output


def _render_summary(reports: list[ModelReport], n_scen: int) -> None:
    table = Table(title="my20Q reasoning bench", show_lines=True)
    table.add_column("model", style="bold")
    table.add_column("status")
    table.add_column("converged", justify="right")
    table.add_column("avg queries", justify="right")
    table.add_column("re-asks", justify="right")
    table.add_column("fallback", justify="right")
    table.add_column("p50 lat (s)", justify="right")
    table.add_column("p95 lat (s)", justify="right")

    for r in reports:
        label = r.model if not r.noise else f"{r.model}  ε={r.noise:g}"
        if not r.available:
            table.add_row(label, f"[yellow]{r.error}[/yellow]", *(["-"] * 6))
            continue
        conv_rounds = [x for x in r.rounds if x.converged]
        avg_q = (
            statistics.mean(x.queries for x in conv_rounds) if conv_rounds else float("nan")
        )
        # Gate rejections replace the old `reasks`, which counted rationale
        # prefixes the v3 engine no longer emits (it always read 0).
        rejections = sum(x.metrics.get("gate_rejections", 0) for x in r.rounds)
        lats = sorted(r.latencies)
        p50 = statistics.median(lats) if lats else float("nan")
        p95 = lats[min(len(lats) - 1, int(len(lats) * 0.95))] if lats else float("nan")
        conv_style = "green" if r.converged == n_scen else "yellow" if r.converged else "red"
        table.add_row(
            label,
            "[green]ok[/green]",
            f"[{conv_style}]{r.converged}/{n_scen}[/{conv_style}]",
            "—" if conv_rounds == [] else f"{avg_q:.1f}",
            str(rejections),
            f"{r.degraded}/{n_scen}",
            f"{p50:.1f}",
            f"{p95:.1f}",
        )
    console.print(table)


#: Metrics where a LOWER value is the improvement — used only to colour the
#: --compare arrows; convergence is handled separately (higher is better).
_LOWER_IS_BETTER = {
    "queries_to_converge",
    "ready_at_query",
    "queries_after_ready",
    "restarts",
    "diagnostics",
    "gate_rejections",
    "latency_ms",
}


def _render_convergence(reports: list[ModelReport]) -> None:
    """The §1 autopsy table, automated — one row per run (W2-G)."""
    rows = [(r, r.summary) for r in reports if r.available and r.rounds]
    if not rows:
        return
    table = Table(title="convergence metrics (from the round records)", show_lines=True)
    table.add_column("run", style="bold")
    table.add_column("conv", justify="right")
    table.add_column("q/conv", justify="right")
    table.add_column("after ready", justify="right")
    table.add_column("info-yes", justify="right")
    table.add_column("restarts", justify="right")
    table.add_column("diags", justify="right")
    table.add_column("gate rej", justify="right")
    table.add_column("top focus")

    def _fmt(v: object) -> str:
        if v is None:
            return "—"
        return f"{v:.2f}" if isinstance(v, float) else str(v)

    for r, s in rows:
        label = r.model if not r.noise else f"{r.model}  ε={r.noise:g}"
        focus = Counter(s.get("focus") or {}).most_common(3)
        table.add_row(
            label,
            f"{s['converged']}/{s['rounds']}",
            _fmt(s["queries_to_converge"]),
            _fmt(s["queries_after_ready"]),
            _fmt(s["informative_yes_ratio"]),
            _fmt(s["restarts"]),
            _fmt(s["diagnostics"]),
            _fmt(s["gate_rejections"]),
            "  ".join(f"{c}={n}" for c, n in focus) or "—",
        )
    console.print(table)
    console.print(
        "[dim]‘after ready’ = questions asked once the draft was already "
        "offerable — the wasted-query estimate. Needs W2-O instrumentation.[/dim]"
    )


# --------------------------------------------------------------- compare


def _run_key(report: dict) -> str:
    return f"{report.get('model')}  ε={report.get('noise', 0.0):g}"


def compare_runs(before: list[dict], after: list[dict]) -> list[dict]:
    """Diff two `--json` result files, run by run (W2-G).

    Matches on (model, noise), so the same engine measured before and after a
    change lines up. Runs present in only one file are reported with the
    missing side as ``None`` rather than dropped — a run that stopped happening
    is a result too.
    """
    def _summaries(reports: list[dict]) -> dict[str, dict]:
        return {
            _run_key(r): aggregate_metrics(
                [x["metrics"] for x in r.get("rounds", []) if x.get("metrics")]
            )
            for r in reports
        }

    b, a = _summaries(before), _summaries(after)
    out: list[dict] = []
    for key in sorted(set(b) | set(a)):
        bm, am = b.get(key) or {}, a.get(key) or {}
        deltas: dict[str, dict] = {}
        for metric in sorted(set(bm) | set(am)):
            if metric == "focus":
                continue
            bv, av = bm.get(metric), am.get(metric)
            if not isinstance(bv, (int, float)) or not isinstance(av, (int, float)):
                deltas[metric] = {"before": bv, "after": av, "delta": None}
                continue
            deltas[metric] = {
                "before": bv,
                "after": av,
                "delta": round(av - bv, 3),
            }
        out.append({"run": key, "metrics": deltas})
    return out


def _render_compare(diff: list[dict]) -> None:
    for entry in diff:
        table = Table(title=f"Δ  {entry['run']}", show_lines=False)
        table.add_column("metric", style="bold")
        table.add_column("before", justify="right")
        table.add_column("after", justify="right")
        table.add_column("Δ", justify="right")
        for metric, d in entry["metrics"].items():
            delta = d["delta"]
            if delta is None:
                cell = "—"
            elif delta == 0:
                cell = "[dim]0[/dim]"
            else:
                better = (delta < 0) is (metric in _LOWER_IS_BETTER)
                style = "green" if better else "red"
                cell = f"[{style}]{delta:+g}[/{style}]"
            table.add_row(
                metric,
                "—" if d["before"] is None else f"{d['before']:g}",
                "—" if d["after"] is None else f"{d['after']:g}",
                cell,
            )
        console.print(table)


def _render_transcripts(reports: list[ModelReport], scenarios: list[Scenario]) -> None:
    available = [r for r in reports if r.available]
    # Keyed by RUN, not model: a noise sweep runs the same model several times.
    by_run = [
        (r.model if not r.noise else f"{r.model}  ε={r.noise:g}",
         {x.scenario: x for x in r.rounds})
        for r in available
    ]
    for sc in scenarios:
        console.rule(f"[bold]{sc.name}[/bold]  ·  {sc.topic_id}")
        console.print(f"[dim]hidden need:[/dim] [italic]{sc.need}[/italic]\n")
        for model, rounds in by_run:
            res = rounds.get(sc.name)
            if res is None:
                continue
            mark = "[green]✓[/green]" if res.converged else "[red]✗[/red]"
            tag = " [yellow](degraded→fallback)[/yellow]" if res.degraded else ""
            if res.flipped:
                tag += f" [magenta]({res.flipped} answer(s) flipped)[/magenta]"
            console.print(f"{mark} [bold cyan]{model}[/bold cyan]  ({res.outcome}){tag}")
            if res.degraded and res.degrade_reason:
                console.print(f"    [yellow]degrade reason:[/yellow] {res.degrade_reason}")
            for line in res.transcript:
                console.print(f"    {line}")
            console.print()


async def _run(
    models: list[str],
    simulator: str,
    max_queries: int,
    temperature: float,
    noise_levels: list[float],
    seed: int,
    scenarios: list[Scenario],
) -> list[ModelReport]:
    topics = load_topics()
    sim = _make_simulator(simulator)
    # Pre-load a local simulator so it co-resides with the first candidate.
    with contextlib.suppress(LLMUnavailable):
        await sim.chat([{"role": "user", "content": "Reply: ready"}], max_tokens=4)
    reports: list[ModelReport] = []
    for model in models:
        for noise in noise_levels:
            tag = f" ε={noise:g}" if noise else ""
            console.print(f"[dim]benchmarking {model}{tag} (sim: {simulator})…[/dim]")
            reports.append(
                await _bench_model(
                    model,
                    sim,
                    topics,
                    scenarios,
                    max_queries,
                    temperature,
                    noise=noise,
                    seed=seed,
                )
            )
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench_reasoning", description=__doc__)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--simulator", default=DEFAULT_SIMULATOR)
    parser.add_argument("--max-queries", type=int, default=12)
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Reasoner sampling temperature (0 = reproducible)",
    )
    parser.add_argument(
        "--noise",
        nargs="+",
        type=float,
        default=[0.0],
        metavar="EPS",
        help="ε-noise levels for the answerer (yes↔no flipped with prob. ε; "
        "kinda/not-sure untouched; the caregiver's accept is never noised). "
        "Each level is a separate run, so '0 0.05 0.1 0.2' costs 4x.",
    )
    parser.add_argument(
        "--scenarios",
        nargs="+",
        metavar="NAME",
        help="Run only these scenarios by name (default: all). Use it to keep "
        "a smoke run short — the full set against every model is long.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Seed for the noise draws — the same seed flips the same answers "
        "across engine revisions, so a --compare delta is real.",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        type=Path,
        metavar=("BEFORE", "AFTER"),
        help="Diff two --json result files instead of running the bench.",
    )
    parser.add_argument("--no-transcripts", action="store_true")
    parser.add_argument("--json", type=Path, help="Write full results as JSON")
    args = parser.parse_args(argv)

    if args.compare:
        before, after = (
            json.loads(p.read_text(encoding="utf-8")) for p in args.compare
        )
        _render_compare(compare_runs(before, after))
        return 0

    if any(e < 0.0 or e > 1.0 for e in args.noise):
        parser.error("--noise levels must be probabilities in [0, 1]")

    scenarios = SCENARIOS
    if args.scenarios:
        wanted = set(args.scenarios)
        unknown = wanted - {s.name for s in SCENARIOS}
        if unknown:
            parser.error(
                f"unknown scenario(s) {sorted(unknown)}; available: "
                + ", ".join(s.name for s in SCENARIOS)
            )
        scenarios = [s for s in SCENARIOS if s.name in wanted]

    reports = asyncio.run(
        _run(
            args.models,
            args.simulator,
            args.max_queries,
            args.temperature,
            args.noise,
            args.seed,
            scenarios,
        )
    )
    _render_summary(reports, len(scenarios))
    _render_convergence(reports)
    if not args.no_transcripts:
        _render_transcripts(reports, scenarios)

    if args.json:
        args.json.write_text(
            json.dumps([asdict(r) for r in reports], indent=2), encoding="utf-8"
        )
        console.print(f"[dim]wrote {args.json}[/dim]")

    return 0 if any(r.available for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
