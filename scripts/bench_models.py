"""Benchmark candidate Ollama models on my20Q's two LLM tasks.

For each model, runs the rephrase-question and summarize-path prompts a
configurable number of times and reports per-call latency (p50/p95) plus
representative outputs so quality can be eyeballed side-by-side.

Default model list is curated to the LLM families permitted for this
project (Google Gemma, Meta Llama, Microsoft Phi, Mistral, IBM Granite,
Cohere Command-R). Override with --models.

Prerequisites:
  1. ollama serve running (default http://localhost:11434)
  2. The models you want to bench have been pulled with `ollama pull`.
     Models that aren't pulled are skipped with a warning — this script
     does not pull for you (bandwidth is yours to budget).

Usage:
  python scripts/bench_models.py
  python scripts/bench_models.py --models gemma3:4b llama3.2:3b phi3.5
  python scripts/bench_models.py --runs 5 --json out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rich.console import Console
from rich.table import Table

from my20q.agent import prompts
from my20q.agent.safety import sanitize_llm_text
from my20q.llm.base import LLMUnavailable
from my20q.llm.ollama_client import OllamaBackend
from my20q.taxonomy import Node, load_taxonomy

console = Console()

# Curated to excluded-list-compliant families only.
DEFAULT_MODELS: list[str] = [
    "gemma3:1b",
    "gemma3:4b",
    "gemma3:12b",
    "llama3.2:1b",
    "llama3.2:3b",
    "llama3.1:8b",
    "phi3.5",
    "phi4-mini",
    "mistral:7b",
    "granite3.1-dense:2b",
    "granite3.1-dense:8b",
]

# Sample inputs — representative of what Phase 1 actually sends.
REPHRASE_SAMPLES: list[str] = [
    "Are you in pain?",
    "Would you like someone to be with you?",
    "Do you need your medicine?",
    "Are things feeling confusing right now?",
    "Would you like to watch television?",
]


@dataclass
class CallResult:
    prompt_label: str
    input: str
    output: str
    sanitized: str
    latency_s: float
    dropped_by_sanitizer: bool = False


@dataclass
class ModelReport:
    model: str
    available: bool
    error: str = ""
    calls: list[CallResult] = field(default_factory=list)

    @property
    def latencies(self) -> list[float]:
        return [c.latency_s for c in self.calls]

    @property
    def drop_rate(self) -> float:
        if not self.calls:
            return 0.0
        dropped = sum(1 for c in self.calls if c.dropped_by_sanitizer)
        return dropped / len(self.calls)


def _load_sample_paths(tree: Node) -> list[list[Node]]:
    """Build a few representative traversal paths for the summary benchmark."""
    targets = ["ph_pain", "gen_cold", "mh_lonely", "ph_thirsty"]
    paths: list[list[Node]] = []
    for target_id in targets:
        node = tree.find(target_id)
        if node is None:
            continue
        path = _path_to(tree, target_id) or [tree, node]
        paths.append(path)
    return paths


def _path_to(node: Node, target_id: str) -> list[Node] | None:
    if node.id == target_id:
        return [node]
    for child in node.children:
        sub = _path_to(child, target_id)
        if sub is not None:
            return [node, *sub]
    return None


async def _model_available(backend: OllamaBackend) -> tuple[bool, str]:
    """Check /api/tags for the model; return (available, note)."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{backend.base_url}/api/tags")
            resp.raise_for_status()
            tags = resp.json().get("models", [])
    except httpx.HTTPError as exc:
        return False, f"tags check failed: {exc}"
    names = {m.get("name", "") for m in tags}
    # Ollama returns names like 'llama3.2:3b' exactly; also match the implicit :latest form.
    if backend.model in names or f"{backend.model}:latest" in names:
        return True, ""
    if any(n.startswith(f"{backend.model}:") for n in names):
        return True, ""
    return False, f"not pulled (run: ollama pull {backend.model})"


async def _bench_model(model: str, runs: int, paths: list[list[Node]]) -> ModelReport:
    backend = OllamaBackend(model=model)
    report = ModelReport(model=model, available=False)

    if not await backend.health():
        report.error = "ollama not reachable"
        return report
    available, note = await _model_available(backend)
    if not available:
        report.error = note
        return report
    report.available = True

    for _ in range(runs):
        for question in REPHRASE_SAMPLES:
            messages = prompts.rephrase_question_messages(question)
            t0 = time.perf_counter()
            try:
                out = await backend.chat(messages, max_tokens=60)
            except LLMUnavailable as exc:
                report.calls.append(
                    CallResult(
                        prompt_label="rephrase",
                        input=question,
                        output=f"<error: {exc}>",
                        sanitized="",
                        latency_s=time.perf_counter() - t0,
                        dropped_by_sanitizer=False,
                    )
                )
                continue
            dt = time.perf_counter() - t0
            clean = sanitize_llm_text(out)
            report.calls.append(
                CallResult(
                    prompt_label="rephrase",
                    input=question,
                    output=out,
                    sanitized=clean,
                    latency_s=dt,
                    dropped_by_sanitizer=not clean,
                )
            )

        for path in paths:
            messages = prompts.summarize_path_messages(path)
            t0 = time.perf_counter()
            try:
                out = await backend.chat(messages, max_tokens=80)
            except LLMUnavailable as exc:
                report.calls.append(
                    CallResult(
                        prompt_label="summary",
                        input=" > ".join(n.label for n in path if n.id != "root"),
                        output=f"<error: {exc}>",
                        sanitized="",
                        latency_s=time.perf_counter() - t0,
                        dropped_by_sanitizer=False,
                    )
                )
                continue
            dt = time.perf_counter() - t0
            clean = sanitize_llm_text(out)
            report.calls.append(
                CallResult(
                    prompt_label="summary",
                    input=" > ".join(n.label for n in path if n.id != "root"),
                    output=out,
                    sanitized=clean,
                    latency_s=dt,
                    dropped_by_sanitizer=not clean,
                )
            )

    return report


def _render_summary(reports: list[ModelReport]) -> None:
    table = Table(title="my20Q model bench", show_lines=True)
    table.add_column("model", style="bold")
    table.add_column("status")
    table.add_column("n", justify="right")
    table.add_column("p50 (s)", justify="right")
    table.add_column("p95 (s)", justify="right")
    table.add_column("max (s)", justify="right")
    table.add_column("sanitizer drops", justify="right")

    for r in reports:
        if not r.available:
            table.add_row(r.model, f"[yellow]{r.error}[/yellow]", "-", "-", "-", "-", "-")
            continue
        lats = sorted(r.latencies)
        if not lats:
            table.add_row(r.model, "[red]no calls[/red]", "0", "-", "-", "-", "-")
            continue
        p50 = statistics.median(lats)
        p95 = lats[min(len(lats) - 1, int(len(lats) * 0.95))]
        table.add_row(
            r.model,
            "[green]ok[/green]",
            str(len(lats)),
            f"{p50:.2f}",
            f"{p95:.2f}",
            f"{max(lats):.2f}",
            f"{r.drop_rate:.0%}",
        )
    console.print(table)


def _render_samples(reports: list[ModelReport]) -> None:
    for r in reports:
        if not r.available or not r.calls:
            continue
        console.rule(f"[bold]{r.model}[/bold]")
        seen_inputs: set[tuple[str, str]] = set()
        for call in r.calls:
            key = (call.prompt_label, call.input)
            if key in seen_inputs:
                continue
            seen_inputs.add(key)
            tag = "[red](DROPPED)[/red] " if call.dropped_by_sanitizer else ""
            console.print(
                f"[dim]{call.prompt_label}[/dim] "
                f"[cyan]{call.input}[/cyan]\n"
                f"  {tag}{call.sanitized or call.output}"
            )


def _default_taxonomy_path() -> Path:
    root = Path(__file__).resolve().parent.parent
    return root / "src" / "my20q" / "taxonomy" / "data" / "tree.yaml"


async def _run(models: list[str], runs: int, taxonomy_path: Path) -> list[ModelReport]:
    tree = load_taxonomy(taxonomy_path)
    paths = _load_sample_paths(tree)
    reports: list[ModelReport] = []
    for model in models:
        console.print(f"[dim]benchmarking {model}...[/dim]")
        reports.append(await _bench_model(model, runs, paths))
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench_models", description=__doc__)
    parser.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_MODELS,
        help="Model tags to bench (defaults to the curated list)",
    )
    parser.add_argument("--runs", type=int, default=2, help="Full passes over the sample inputs")
    parser.add_argument(
        "--taxonomy",
        type=Path,
        default=_default_taxonomy_path(),
        help="Path to taxonomy YAML",
    )
    parser.add_argument("--json", type=Path, help="Optional: write full results as JSON")
    args = parser.parse_args(argv)

    reports = asyncio.run(_run(args.models, args.runs, args.taxonomy))
    _render_summary(reports)
    _render_samples(reports)

    if args.json:
        args.json.write_text(
            json.dumps([asdict(r) for r in reports], indent=2),
            encoding="utf-8",
        )
        console.print(f"[dim]wrote {args.json}[/dim]")

    any_available = any(r.available for r in reports)
    return 0 if any_available else 1


if __name__ == "__main__":
    raise SystemExit(main())
