"""Rich CLI harness for the my20Q dialogue engine.

A developer tool, not patient-facing — the caregiver cockpit (web) is the
real interface. Plays rounds against the engine: pick a topic, answer
y/n/k/s (u to undo, q to quit), watch the reasoning, see the synthesized
utterance.

    python -m my20q              # reasoning mode (needs Ollama)
    python -m my20q --no-llm     # deterministic fallback mode
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import replace

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from my20q.agent.dialogue import Answer, RoundEvent, Session
from my20q.agent.safety import EMERGENCY_SCREEN
from my20q.config import Config
from my20q.llm import BackendRefused, select_backend
from my20q.profiles import load_profile
from my20q.topics import Topic, load_topics

console = Console()

_ANSWERS = {"y": Answer.YES, "n": Answer.NO, "k": Answer.KINDA, "s": Answer.NOT_SURE}


class _Quit(Exception):
    """Raised when the user asks to quit at a prompt."""


def _pick_topic(topics: list[Topic]) -> Topic:
    console.print(Panel.fit("[bold]my20Q[/bold] — pick a topic", style="cyan"))
    for i, topic in enumerate(topics, start=1):
        tag = " [red](emergency)[/red]" if topic.emergency else ""
        console.print(f"  {i}. {topic.label}{tag}  [dim]({topic.id})[/dim]")
    console.print("  q. quit")
    choices = [str(i) for i in range(1, len(topics) + 1)] + ["q"]
    choice = Prompt.ask("Topic", choices=choices)
    if choice == "q":
        raise _Quit
    return topics[int(choice) - 1]


def _render_event(event: RoundEvent) -> None:
    if event.kind == "query":
        body = f"[dim]query {event.query_index} · {event.engine}[/dim]\n[bold]{event.text}[/bold]"
        if event.rationale:
            body += f"\n[dim italic]{event.rationale}[/dim italic]"
        console.print(Panel.fit(body, style="cyan"))
    elif event.kind == "synthesis":
        console.print(
            Panel.fit(
                f"[dim]proposed message — 'y' confirms[/dim]\n[bold]“{event.text}”[/bold]",
                style="magenta",
            )
        )


def _render_emergency() -> None:
    body = f"[bold]{EMERGENCY_SCREEN['title']}[/bold]\n\n{EMERGENCY_SCREEN['body']}\n\n"
    body += "\n".join(f" * {a['label']}" for a in EMERGENCY_SCREEN["actions"])
    console.print(Panel(body, style="red", title="GET HELP"))


def _ask_answer() -> str:
    raw = Prompt.ask(
        "[bold]y/n/k/s[/bold] [dim](u undo · q quit)[/dim]",
        choices=["y", "n", "k", "s", "u", "q"],
        default="y",
    )
    if raw == "q":
        raise _Quit
    return raw


async def _play_round(session: Session, topic: Topic) -> None:
    rnd = session.start_round(topic.id)
    event = await rnd.open()
    while True:
        if event.kind == "emergency":
            _render_emergency()
            return
        if event.kind == "synthesized":
            console.print(
                Panel.fit(f"[bold]Synthesized:[/bold] “{event.text}”", style="green")
            )
            return
        if event.kind == "abandoned":
            console.print(
                Panel("Round ended without a confirmed message.", style="yellow")
            )
            return
        _render_event(event)
        raw = _ask_answer()
        event = await (rnd.undo() if raw == "u" else rnd.answer(_ANSWERS[raw]))


async def _run(cfg: Config) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    topics = load_topics(cfg.topics_path)
    profile = load_profile(cfg.profile_path)
    try:
        backend = select_backend(cfg, profile)
    except BackendRefused as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    if backend is None:
        console.print("[dim]No LLM — deterministic fallback mode.[/dim]")
    elif not await backend.health():
        console.print("[yellow]LLM backend unreachable — using fallback mode.[/yellow]")
        backend = None
    else:
        console.print("[green]LLM backend ready.[/green]")

    session = Session(topics, llm=backend, config=cfg, profile=profile)
    while True:
        try:
            await _play_round(session, _pick_topic(topics))
        except _Quit:
            console.print("[dim]goodbye[/dim]")
            return 0
        if Prompt.ask("\n[bold]Play again?[/bold]", choices=["y", "n"], default="y") == "n":
            console.print("[dim]goodbye[/dim]")
            return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="my20q", description=__doc__)
    parser.add_argument(
        "--no-llm", action="store_true", help="Disable the LLM (fallback mode only)"
    )
    parser.add_argument(
        "--max-queries", type=int, default=None, help="Override the per-round query budget"
    )
    args = parser.parse_args(argv)

    cfg = Config.from_env()
    if args.no_llm:
        cfg = replace(cfg, llm_enabled=False)
    if args.max_queries is not None:
        cfg = replace(cfg, max_queries=args.max_queries)
    try:
        return asyncio.run(_run(cfg))
    except KeyboardInterrupt:
        console.print("\n[dim]cancelled[/dim]")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
