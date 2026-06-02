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
every model equally; its answers are logged so you can audit them. Run it at
temperature 0 for reproducibility (the default here).

Prerequisites:
  1. ``ollama serve`` running (default http://localhost:11434).
  2. The candidate + simulator models are pulled (`ollama pull`). Models that
     aren't pulled are skipped with a note — this script never pulls for you.

Usage:
  python scripts/bench_reasoning.py
  python scripts/bench_reasoning.py --models gemma3:12b gemma4:e4b
  python scripts/bench_reasoning.py --simulator gemma3:4b --max-queries 12
  python scripts/bench_reasoning.py --json out.json --no-transcripts
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rich.console import Console
from rich.table import Table

from my20q.agent.dialogue import Answer, Round
from my20q.llm.base import LLMUnavailable
from my20q.llm.ollama_client import OllamaBackend
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
    "gemma3:12b",  # current production default
    "gemma4:e4b",
    "gemma4:26b",  # spills past 16 GB VRAM — slow, but tests "bigger reasons better"
    "gemma3:4b",  # baseline
    "llama3.2:3b",  # baseline
]

#: The patient simulator — held constant across all candidates. Small + fast,
#: because it is called once per query. Override with --simulator.
DEFAULT_SIMULATOR = "gemma3:4b"


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
]


# ------------------------------------------------------------- simulator

_SIM_SYSTEM = """\
You role-play a person with aphasia who is trying to communicate ONE specific
need to a caregiver. You answer the caregiver's yes/no questions truthfully,
about THIS need only:

  YOUR NEED: {need}

Reply with EXACTLY ONE word, lowercase, no punctuation:
- "yes"      — the question is true of your need.
- "no"       — the question is false of your need.
- "kinda"    — partly true / on the right track but not exact.
- "not_sure" — the question is unrelated to your need.
Never explain. One word only.
"""

_SIM_CONFIRM = """\
The caregiver thinks you are trying to say:
  "{utterance}"

Your actual need is: {need}
Does that sentence correctly capture your need? Reply EXACTLY one word:
"yes" or "no". One word only.
"""

_ANSWER_WORDS = {a.value: a for a in Answer}


async def _simulate_answer(sim: OllamaBackend, need: str, question: str) -> Answer:
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


async def _simulate_confirm(sim: OllamaBackend, need: str, utterance: str) -> bool:
    messages = [
        {"role": "system", "content": "Answer with exactly one word: yes or no."},
        {"role": "user", "content": _SIM_CONFIRM.format(utterance=utterance, need=need)},
    ]
    try:
        raw = await sim.chat(messages, max_tokens=4)
    except LLMUnavailable:
        return False
    return raw.strip().lower().startswith("y")


# --------------------------------------------------------------- runner


@dataclass
class RoundResult:
    scenario: str
    converged: bool
    outcome: str | None
    queries: int
    reasks: int
    degraded: bool  # the candidate fell back to deterministic mode mid-round
    final_utterance: str
    transcript: list[str] = field(default_factory=list)
    reasoner_latencies: list[float] = field(default_factory=list)


@dataclass
class ModelReport:
    model: str
    available: bool
    error: str = ""
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


_ANSWER_LABEL = {a: a.value for a in Answer}


async def _drive_round(
    *,
    reasoner: OllamaBackend,
    sim: OllamaBackend,
    topic: Topic,
    scenario: Scenario,
    max_queries: int,
) -> RoundResult:
    rnd = Round(topic, llm=reasoner, max_queries=max_queries)
    transcript: list[str] = []
    latencies: list[float] = []

    async def step(coro):
        t0 = time.perf_counter()
        ev = await coro  # candidate reasoner inference happens here
        latencies.append(time.perf_counter() - t0)
        return ev

    ev = await step(rnd.open())
    # Hard cap on iterations so a pathological loop can't run forever.
    for _ in range(max_queries * 2 + 4):
        if rnd.is_terminal:
            break
        if ev.kind == "query":
            ans = await _simulate_answer(sim, scenario.need, ev.text)
            transcript.append(f"Q{ev.query_index}: {ev.text}  → {ans.value}")
            ev = await step(rnd.answer(ans))
        elif ev.kind == "synthesis":
            ok = await _simulate_confirm(sim, scenario.need, ev.text)
            transcript.append(f"SYN: “{ev.text}”  → {'confirm' if ok else 'reject'}")
            ev = await step(rnd.answer(Answer.YES if ok else Answer.NO))
        else:
            break

    if ev.kind == "synthesized" or rnd.outcome == "synthesized":
        transcript.append(f"✓ CONFIRMED: “{rnd.final_utterance}”")

    reasks = sum(
        1
        for h in rnd.history
        if h["kind"] == "query"
        and str(h.get("rationale", "")).startswith(("(re-asked", "(auditor"))
    )
    return RoundResult(
        scenario=scenario.name,
        converged=rnd.outcome == "synthesized",
        outcome=rnd.outcome,
        queries=rnd.query_count,
        reasks=reasks,
        degraded=rnd.engine == "fallback",
        final_utterance=rnd.final_utterance,
        transcript=transcript,
        reasoner_latencies=latencies,
    )


async def _model_pulled(backend: OllamaBackend) -> bool:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{backend.base_url}/api/tags")
            resp.raise_for_status()
            names = {m.get("name", "") for m in resp.json().get("models", [])}
    except httpx.HTTPError:
        return False
    return backend.model in names or any(n == backend.model for n in names)


async def _bench_model(
    model: str,
    sim: OllamaBackend,
    topics: list[Topic],
    scenarios: list[Scenario],
    max_queries: int,
    temperature: float,
) -> ModelReport:
    # Generous timeout: gemma4:26b spills past 16 GB VRAM and runs partly on
    # CPU, so a single call can be slow — don't unfairly clip it to fallback.
    reasoner = OllamaBackend(model=model, timeout_s=240.0, temperature=temperature)
    report = ModelReport(model=model, available=False)
    if not await reasoner.health():
        report.error = "ollama not reachable"
        return report
    if not await _model_pulled(reasoner):
        report.error = f"not pulled (ollama pull {model})"
        return report
    report.available = True

    for sc in scenarios:
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
        if not r.available:
            table.add_row(r.model, f"[yellow]{r.error}[/yellow]", *(["-"] * 6))
            continue
        conv_rounds = [x for x in r.rounds if x.converged]
        avg_q = (
            statistics.mean(x.queries for x in conv_rounds) if conv_rounds else float("nan")
        )
        reasks = sum(x.reasks for x in r.rounds)
        lats = sorted(r.latencies)
        p50 = statistics.median(lats) if lats else float("nan")
        p95 = lats[min(len(lats) - 1, int(len(lats) * 0.95))] if lats else float("nan")
        conv_style = "green" if r.converged == n_scen else "yellow" if r.converged else "red"
        table.add_row(
            r.model,
            "[green]ok[/green]",
            f"[{conv_style}]{r.converged}/{n_scen}[/{conv_style}]",
            "—" if conv_rounds == [] else f"{avg_q:.1f}",
            str(reasks),
            f"{r.degraded}/{n_scen}",
            f"{p50:.1f}",
            f"{p95:.1f}",
        )
    console.print(table)


def _render_transcripts(reports: list[ModelReport], scenarios: list[Scenario]) -> None:
    available = [r for r in reports if r.available]
    by_model = {r.model: {x.scenario: x for x in r.rounds} for r in available}
    for sc in scenarios:
        console.rule(f"[bold]{sc.name}[/bold]  ·  {sc.topic_id}")
        console.print(f"[dim]hidden need:[/dim] [italic]{sc.need}[/italic]\n")
        for model, rounds in by_model.items():
            res = rounds.get(sc.name)
            if res is None:
                continue
            mark = "[green]✓[/green]" if res.converged else "[red]✗[/red]"
            tag = " [yellow](degraded→fallback)[/yellow]" if res.degraded else ""
            console.print(f"{mark} [bold cyan]{model}[/bold cyan]  ({res.outcome}){tag}")
            for line in res.transcript:
                console.print(f"    {line}")
            console.print()


async def _run(
    models: list[str], simulator: str, max_queries: int, temperature: float
) -> list[ModelReport]:
    topics = load_topics()
    sim = OllamaBackend(model=simulator, timeout_s=60.0, temperature=0.0)
    reports: list[ModelReport] = []
    for model in models:
        console.print(f"[dim]benchmarking {model} (sim: {simulator})…[/dim]")
        reports.append(
            await _bench_model(model, sim, topics, SCENARIOS, max_queries, temperature)
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
    parser.add_argument("--no-transcripts", action="store_true")
    parser.add_argument("--json", type=Path, help="Write full results as JSON")
    args = parser.parse_args(argv)

    reports = asyncio.run(
        _run(args.models, args.simulator, args.max_queries, args.temperature)
    )
    _render_summary(reports, len(SCENARIOS))
    if not args.no_transcripts:
        _render_transcripts(reports, SCENARIOS)

    if args.json:
        args.json.write_text(
            json.dumps([asdict(r) for r in reports], indent=2), encoding="utf-8"
        )
        console.print(f"[dim]wrote {args.json}[/dim]")

    return 0 if any(r.available for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
