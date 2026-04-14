"""Rich-based CLI harness for iterating on the dialogue engine.

Not patient-facing — this is a developer tool. The PWA (Phase 2) is
what a patient actually uses.

Each session plays until a terminal outcome (confirmed answer, 20-turn
limit, emergency, or dead-end), then offers "play again?" so the agent
loops back to the category picker instead of exiting.
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
from my20q.taxonomy import Node, load_taxonomy

console = Console()


def _build_llm(cfg: Config) -> LLMBackend | None:
    if not cfg.llm_enabled:
        console.print(
            "[dim]LLM disabled. Using deterministic taxonomy walk "
            "(questions only, no reasoning).[/dim]"
        )
        return None
    backend = OllamaBackend(cfg.ollama_base_url, cfg.ollama_model, cfg.ollama_timeout_s)
    if not asyncio.run(backend.health()):
        console.print(
            "[yellow]Ollama not reachable at "
            f"{cfg.ollama_base_url} - continuing in fallback mode.[/yellow]"
        )
        return None
    console.print(f"[green]Using Ollama model [bold]{cfg.ollama_model}[/bold][/green]")
    return backend


FREEFORM_CATEGORY_ID = "general"


def _pick_category(root: Node) -> Node | None:
    console.print(Panel.fit("[bold]my20Q[/bold] - what do you need/want?", style="cyan"))
    for idx, child in enumerate(root.children, start=1):
        tag = " [red](emergency)[/red]" if child.emergency else ""
        console.print(f"  {idx}. {child.label}{tag}  [dim]({child.id})[/dim]")
    console.print("  q. quit")
    choices = [str(i) for i in range(1, len(root.children) + 1)] + ["q"]
    choice = Prompt.ask("Pick a category number (or q to quit)", choices=choices)
    if choice == "q":
        return None
    return root.children[int(choice) - 1]


def _truncate(text: str, limit: int = 60) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _gather_freeform_context(prior_contexts: list[str]) -> str:
    """Prompt the user for the "Other" category seed, optionally prepending
    one or more successful contexts carried over from earlier rounds.
    Entering `q` at any prompt raises _QuitRequested."""
    preamble = ""
    if prior_contexts:
        console.print("\n[bold]Include context from previous rounds?[/bold]")
        for idx, ctx in enumerate(prior_contexts, start=1):
            console.print(f"  {idx}. {_truncate(ctx)}")
        console.print("  a. All of the above")
        console.print("  n. None")
        console.print("  q. quit")
        choices = [str(i) for i in range(1, len(prior_contexts) + 1)] + ["a", "n", "q"]
        choice = Prompt.ask("Pick one (q to quit)", choices=choices, default="n")
        if choice == "q":
            raise _QuitRequested()
        if choice == "a":
            preamble = " | ".join(prior_contexts)
        elif choice != "n":
            preamble = prior_contexts[int(choice) - 1]

    detail = Prompt.ask(
        "[bold]Explain the topic in any level of detail[/bold] [dim](q to quit)[/dim]",
        default="",
    ).strip()
    if detail.lower() == "q":
        raise _QuitRequested()
    parts = [p for p in (preamble, detail) if p]
    return " | ".join(parts)


def _ask_answer() -> Answer:
    raw = Prompt.ask(
        "[bold]yes / no / kinda / not sure[/bold] [dim](q to quit)[/dim]",
        choices=["y", "n", "k", "s", "q"],
        default="y",
    )
    if raw == "q":
        raise _QuitRequested()
    return {
        "y": Answer.YES,
        "n": Answer.NO,
        "k": Answer.KINDA,
        "s": Answer.NOT_SURE,
    }[raw]


def _render_emergency() -> None:
    body = f"[bold]{EMERGENCY_SCREEN['title']}[/bold]\n\n{EMERGENCY_SCREEN['body']}\n\n"
    body += "\n".join(f" * {a['label']}" for a in EMERGENCY_SCREEN["actions"])
    console.print(Panel(body, style="red", title="GET HELP"))


def _render_turn(result: TurnResult) -> None:
    tag = "Guess" if result.kind == "guess" else "Question"
    header = f"[dim]turn {result.turn} - {tag}[/dim]"
    body = f"{header}\n[bold]{result.content}[/bold]"
    if result.rationale:
        body += f"\n[dim italic]why: {result.rationale}[/dim italic]"
    console.print(Panel.fit(body, style="cyan"))


def _render_outcome(result: TurnResult) -> None:
    path_labels = " > ".join(n.label for n in result.path if n.id != "root")
    console.print(
        Panel.fit(
            f"[bold]Summary for caregiver:[/bold]\n{result.summary}\n\n"
            f"[dim]Final: {result.content or path_labels}[/dim]",
            style="green",
        )
    )


def _play_one(
    root: Node,
    llm: LLMBackend | None,
    max_turns: int,
    prior_contexts: list[str],
) -> TurnResult | None:
    """Play one round. Returns the terminal TurnResult (or None when the
    round ended in emergency/dead_end). Raises _QuitRequested if the user
    selected quit at the category picker."""
    chosen = _pick_category(root)
    if chosen is None:
        raise _QuitRequested()

    seed_context = ""
    if chosen.id == FREEFORM_CATEGORY_ID:
        seed_context = _gather_freeform_context(prior_contexts)

    session = DialogueSession(root, llm=llm, max_turns=max_turns, seed_context=seed_context)
    result = session.select_category(chosen.id)

    while True:
        if result.kind == "emergency":
            _render_emergency()
            return None
        if result.kind == "dead_end":
            console.print(
                Panel(
                    "I wasn't able to figure out what you need this round. "
                    "Let's try again.",
                    style="yellow",
                )
            )
            return None
        if result.kind == "answer":
            _render_outcome(result)
            return result
        if result.kind in ("question", "guess"):
            _render_turn(result)
            result = session.answer(_ask_answer())
            continue
        raise AssertionError(f"unhandled turn kind: {result.kind}")


class _QuitRequested(Exception):
    pass


def _run(cfg: Config) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    root = load_taxonomy(cfg.taxonomy_path)
    llm = _build_llm(cfg)
    successes: list[str] = []

    while True:
        try:
            result = _play_one(root, llm, cfg.max_turns, successes)
        except _QuitRequested:
            console.print("[dim]goodbye[/dim]")
            return 0
        if result is not None and result.content:
            successes.append(result.content)
        again = Prompt.ask("\n[bold]Play again?[/bold]", choices=["y", "n"], default="y")
        if again == "n":
            console.print("[dim]goodbye[/dim]")
            return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="my20q", description=__doc__)
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable Ollama (deterministic fallback mode only)",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Override the 20-turn game budget",
    )
    args = parser.parse_args(argv)

    cfg = Config.from_env()
    overrides: dict = {}
    if args.no_llm:
        overrides["llm_enabled"] = False
    if args.max_turns is not None:
        overrides["max_turns"] = args.max_turns
    if overrides:
        cfg = Config(**{**cfg.__dict__, **overrides})
    try:
        return _run(cfg)
    except KeyboardInterrupt:
        console.print("\n[dim]cancelled[/dim]")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
