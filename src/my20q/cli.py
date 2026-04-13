"""Rich-based CLI harness for iterating on the dialogue engine.

Not patient-facing — this is a developer tool. The PWA (Phase 2) is what a
patient actually uses.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from my20q.agent.dialogue import Answer, DialogueSession, TurnResult
from my20q.agent.safety import EMERGENCY_SCREEN
from my20q.config import Config
from my20q.llm.base import LLMBackend
from my20q.llm.ollama_client import OllamaBackend
from my20q.taxonomy import load_taxonomy

console = Console()


def _build_llm(cfg: Config) -> LLMBackend | None:
    if not cfg.llm_enabled:
        console.print("[dim]LLM disabled (MY20Q_LLM=0). Using static phrasing.[/dim]")
        return None
    backend = OllamaBackend(cfg.ollama_base_url, cfg.ollama_model, cfg.ollama_timeout_s)
    if not asyncio.run(backend.health()):
        console.print(
            "[yellow]Ollama not reachable at "
            f"{cfg.ollama_base_url} — continuing with static phrasing.[/yellow]"
        )
        return None
    console.print(f"[green]Using Ollama model [bold]{cfg.ollama_model}[/bold][/green]")
    return backend


def _pick_category(session: DialogueSession) -> TurnResult:
    console.print(Panel.fit("[bold]my20Q[/bold] - what do you need?", style="cyan"))
    for idx, child in enumerate(session.root.children, start=1):
        tag = " [red](emergency)[/red]" if child.emergency else ""
        console.print(f"  {idx}. {child.label}{tag}  [dim]({child.id})[/dim]")
    choice = Prompt.ask(
        "Pick a category number",
        choices=[str(i) for i in range(1, len(session.root.children) + 1)],
    )
    chosen = session.root.children[int(choice) - 1]
    return session.select_category(chosen.id)


def _ask_answer() -> Answer:
    raw = Prompt.ask("[bold]yes / no / not sure[/bold]", choices=["y", "n", "s"], default="y")
    return {"y": Answer.YES, "n": Answer.NO, "s": Answer.NOT_SURE}[raw]


def _render_emergency() -> None:
    body = f"[bold]{EMERGENCY_SCREEN['title']}[/bold]\n\n{EMERGENCY_SCREEN['body']}\n\n"
    body += "\n".join(f" * {a['label']}" for a in EMERGENCY_SCREEN["actions"])
    console.print(Panel(body, style="red", title="GET HELP"))


def _run(cfg: Config) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    root = load_taxonomy(cfg.taxonomy_path)
    session = DialogueSession(root, llm=_build_llm(cfg), max_turns=cfg.max_turns)
    result = _pick_category(session)

    while True:
        if result.kind == "emergency":
            _render_emergency()
            return 0
        if result.kind == "dead_end":
            console.print(
                Panel(
                    "I wasn't able to figure out what you need. Let's start over.",
                    style="yellow",
                )
            )
            return 0
        if result.kind == "answer":
            path_labels = " > ".join(n.label for n in result.path if n.id != "root")
            console.print(
                Panel.fit(
                    f"[bold]Summary for caregiver:[/bold]\n{result.summary}\n\n"
                    f"[dim]Path: {path_labels}[/dim]",
                    style="green",
                )
            )
            return 0
        if result.kind == "confirm_leaf":
            console.print(Panel.fit(f"Is this it? [bold]{result.node.label}[/bold]", style="cyan"))
            result = session.answer(_ask_answer())
            continue
        if result.kind == "question":
            console.print(Panel.fit(result.question, style="cyan"))
            result = session.answer(_ask_answer())
            continue
        raise AssertionError(f"Unhandled turn kind: {result.kind}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="my20q", description=__doc__)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable Ollama (static phrasing only)",
    )
    args = parser.parse_args(argv)

    cfg = Config.from_env()
    if args.no_llm:
        cfg = Config(**{**cfg.__dict__, "llm_enabled": False})
    try:
        return _run(cfg)
    except KeyboardInterrupt:
        console.print("\n[dim]cancelled[/dim]")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
