"""The 5W1H facet board — consensus scores over Who/What/When/Where/Why/How.

The round's belief is a BOARD: for each facet category of the need —

- **who**   — the person involved (helper, visitor, someone to contact),
- **what**  — the subject or object (a drink, a picture, a phone call),
- **when**  — the timing, when it matters (now, soon, after lunch),
- **where** — the place, when it matters (my room, the kitchen, their house),
- **why**   — the motivation (thirsty, redecorating, missing them),
- **how**   — the action wanted (bring it, move it, call them, sit with me),

a set of CONTENDER values with **additive consensus points** (not a normalized
probability). Each answer adds points to the (category, value) pairs the
question actually asserted:

- **yes**   → strong positive,
- **kinda** → softer "warm" signal,
- **no**    → subtracts from the targeted pairs only — it NEVER promotes the
  others (no renormalization), so a contender can only rise by being
  *confirmed*, never by rivals being ruled out,
- **not_sure** → no information, no change.

Crediting is anchored to the question TEXT: a pair can only earn points when
the question literally mentions the value (see :func:`mentions`) — the
structural fix for score drift onto subjects that were never asked about.
Rejected values stay on the board at negative scores, so a later question
cannot silently resurrect them at zero.

Pure and deterministic; the round engine recomputes the board from history,
so undo is pop-and-recompute. Same scoring philosophy as the retired
single-list belief (docs/design/reasoning-retro.md §4), now per category.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

#: The facet categories, in display order.
CATEGORIES: tuple[str, ...] = ("who", "what", "when", "where", "why", "how")
CATEGORY_LABEL: dict[str, str] = {
    "who": "Who",
    "what": "What",
    "when": "When",
    "where": "Where",
    "why": "Why",
    "how": "How",
}

#: Points a single answer adds to each (category, value) a question asserted.
YES_POINTS = 1.0
KINDA_POINTS = 0.5  # "warm" — nearly correct
NO_POINTS = 1.0  # subtracted from the TARGETED pairs only (never promotes others)
#: Strong positive when a caregiver note implies or confirms a value.
CONTEXT_POINTS = 2.0
#: A value at or below this score is treated as rejected and drops out of play
#: (it stays on the board so it cannot be re-minted fresh at zero).
ELIMINATE_FLOOR = -2.0
#: Live values listed per category in prompts / the cockpit tile.
MAX_LISTED = 4
#: Ceiling on contenders held per category (keeps prompts bounded).
MAX_PER_CATEGORY = 6

#: category -> {value: points}. Values keep their original casing; identity is
#: case-insensitive (see _find).
Board = dict[str, dict[str, float]]


def empty_board() -> Board:
    return {cat: {} for cat in CATEGORIES}


def copy_board(board: Board) -> Board:
    return {cat: dict(vals) for cat, vals in board.items()}


def seed_board(seeds: Mapping[str, list[str]]) -> Board:
    """A zero-scored board from per-category seed values (unknown cats dropped)."""
    board = empty_board()
    for cat, values in seeds.items():
        if cat not in board:
            continue
        for value in values:
            _mint(board, cat, value)
    return board


def _find(bucket: dict[str, float], value: str) -> str | None:
    """The existing key matching `value` case-insensitively, if any."""
    needle = value.casefold().strip()
    for key in bucket:
        if key.casefold().strip() == needle:
            return key
    return None


def _mint(board: Board, cat: str, value: str) -> str | None:
    """Ensure `value` exists in `cat` (at 0.0 if new); return its canonical key."""
    value = " ".join(value.split())
    if not value or cat not in board:
        return None
    bucket = board[cat]
    key = _find(bucket, value)
    if key is None:
        bucket[value] = 0.0
        key = value
    return key


def update(board: Board, slots: Mapping[str, str], answer: str) -> Board:
    """Add points to the asserted (category, value) pairs; return a new board.

    No normalization. A "no" subtracts from the pairs the question pointed at
    and leaves everything else untouched. "not_sure" carries no information.
    Unknown values are minted first (so a "no" to a fresh value records it
    NEGATIVE — it stays known-rejected instead of vanishing).
    """
    out = copy_board(board)
    delta = {"yes": YES_POINTS, "kinda": KINDA_POINTS, "no": -NO_POINTS}.get(answer)
    if delta is None:
        return out
    for cat, value in slots.items():
        if not isinstance(value, str) or not value.strip():
            continue
        key = _mint(out, cat, value)
        if key is not None:
            out[cat][key] += delta
    return out


def apply_context(board: Board, slots: Mapping[str, list[str]]) -> Board:
    """Fold a high-trust caregiver note in as a strong additive positive."""
    out = copy_board(board)
    for cat, values in slots.items():
        for value in values:
            key = _mint(out, cat, value)
            if key is not None:
                out[cat][key] += CONTEXT_POINTS
    return out


def merge_values(
    board: Board, seeds: Mapping[str, list[str]], *, cap: int = MAX_PER_CATEGORY
) -> Board:
    """Mint fresh zero-scored values into the board, up to `cap` per category.

    Existing values (case-insensitive) are left untouched — their earned
    scores are never reset by a re-seed.
    """
    out = copy_board(board)
    for cat, values in seeds.items():
        if cat not in out:
            continue
        for value in values:
            if len(out[cat]) >= cap and _find(out[cat], value) is None:
                continue
            _mint(out, cat, value)
    return out


def restore(snapshot: Mapping[str, list[list]]) -> Board:
    """Rebuild a board from a restart marker's ``{cat: [[value, score], ...]}``."""
    board = empty_board()
    for cat, pairs in snapshot.items():
        if cat not in board:
            continue
        for pair in pairs:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                value, score = pair
                key = _mint(board, cat, str(value))
                if key is not None:
                    board[cat][key] = float(score)
    return board


def snapshot(board: Board) -> dict[str, list[list]]:
    """The JSON-serializable form carried by a restart marker."""
    return {cat: [[v, s] for v, s in vals.items()] for cat, vals in board.items() if vals}


def live(board: Board, cat: str) -> list[tuple[str, float]]:
    """In-play (above the eliminate floor) values of `cat`, highest first."""
    items = [(v, s) for v, s in board.get(cat, {}).items() if s > ELIMINATE_FLOOR]
    return sorted(items, key=lambda t: t[1], reverse=True)


def leader(board: Board, cat: str) -> tuple[str, float] | None:
    ranked = live(board, cat)
    return ranked[0] if ranked else None


def confident(board: Board, cat: str, *, ready_points: float, margin: float) -> bool:
    """Whether `cat` has a clear, positively-confirmed leading value.

    The leader must have at least ``ready_points`` and lead the runner-up by at
    least ``margin`` (a sole live value satisfies the margin trivially).
    """
    ranked = live(board, cat)
    if not ranked or ranked[0][1] < ready_points:
        return False
    if len(ranked) == 1:
        return True
    return (ranked[0][1] - ranked[1][1]) >= margin


def tied_top(
    board: Board, cat: str, *, margin: float
) -> tuple[tuple[str, float], tuple[str, float]] | None:
    """The top two live values when both are positive and within `margin`."""
    ranked = live(board, cat)
    if len(ranked) < 2:
        return None
    (v1, s1), (v2, s2) = ranked[0], ranked[1]
    if s1 > 0 and s2 > 0 and (s1 - s2) < margin:
        return (v1, s1), (v2, s2)
    return None


# --------------------------------------------------- question-text anchoring

#: Filler tokens that don't identify a contender on their own.
_STOPWORDS = frozenset(
    [
        "the",
        "a",
        "an",
        "my",
        "your",
        "their",
        "his",
        "her",
        "our",
        "this",
        "that",
        "to",
        "of",
        "for",
        "with",
        "and",
        "or",
        "in",
        "on",
        "at",
        "is",
        "are",
        "was",
        "it",
        "be",
        "do",
        "does",
        "did",
        "need",
        "needs",
        "want",
        "wants",
        "i",
        "me",
        "you",
        "they",
        "them",
        "we",
        "us",
        "someone",
        "something",
        "somewhere",
    ]
)
_TOKEN_RE = re.compile(r"[a-z0-9']+")
_SUFFIXES = ("ing", "es", "ed", "s")


def _stem(token: str) -> str:
    for suf in _SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 3:
            return token[: -len(suf)]
    return token


def _content_tokens(text: str) -> set[str]:
    return {
        _stem(t) for t in _TOKEN_RE.findall(text.casefold()) if len(t) >= 3 and t not in _STOPWORDS
    }


def _tokens_match(a: str, b: str) -> bool:
    """Stemmed tokens match exactly or as prefixes ("mov"/"move", "lift"/"lifting")."""
    if a == b:
        return True
    lo, hi = sorted((a, b), key=len)
    return len(lo) >= 3 and len(hi) >= 4 and hi.startswith(lo)


def mentions(text: str, value: str) -> bool:
    """Whether `text` actually says `value` — the crediting anchor.

    True when any content token of the value appears (stemmed, prefix-tolerant)
    in the text; a value with no content tokens (e.g. "help") falls back to a
    substring check. This is what stops an answer's points from landing on a
    contender the question never mentioned.
    """
    vtok = _content_tokens(value)
    if not vtok:
        return value.casefold().strip() in text.casefold()
    ttok = _content_tokens(text)
    return any(_tokens_match(v, t) for v in vtok for t in ttok)


def overlap_ratio(a: str, b: str) -> float:
    """Fraction of `a`'s content tokens with a match in `b` (0 when none)."""
    ta = _content_tokens(a)
    if not ta:
        return 0.0
    tb = _content_tokens(b)
    hits = sum(1 for x in ta if any(_tokens_match(x, y) for y in tb))
    return hits / len(ta)


def canonical_value(board: Board, cat: str, value: str) -> str:
    """Map a freshly-tagged value onto an existing contender when it's the same.

    Case-insensitive equality or strong token overlap folds variants together
    ("the picture" → "a picture"), so points accrue to one contender instead of
    fragmenting across rewordings. Returns `value` unchanged when nothing
    matches.
    """
    bucket = board.get(cat, {})
    exact = _find(bucket, value)
    if exact is not None:
        return exact
    tokens = _content_tokens(value)
    if not tokens:
        return value
    best, best_score = None, 0.0
    for existing in bucket:
        etok = _content_tokens(existing)
        if not etok:
            continue
        inter = len(tokens & etok) / max(len(tokens), len(etok))
        if inter > best_score:
            best, best_score = existing, inter
    if best is not None and best_score >= 0.5:
        return best
    return value


def facet_view(board: Board, focus: str = "") -> list[dict]:
    """The cockpit tile payload: every category with its top live contenders.

    Raw accumulated points (can be 0 / negative for shown leaders' rivals) —
    that is the actual consensus right now, honestly displayed.
    """
    view: list[dict] = []
    for cat in CATEGORIES:
        contenders = [{"value": v, "score": round(s, 2)} for v, s in live(board, cat)[:MAX_LISTED]]
        view.append(
            {
                "category": cat,
                "label": CATEGORY_LABEL[cat],
                "contenders": contenders,
                "focus": cat == focus,
            }
        )
    return view
