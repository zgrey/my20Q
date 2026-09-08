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


#: Contentless nouns that identify nothing on their own. Tagged as a value
#: they become placeholder contenders that compete with the real answer — the
#: 09-01 round carried `what: feeling` at +3.0, within 1.0 of leading the slot
#: against `pain`. Only rejected when the value is NOTHING BUT these words:
#: "feeling cold" and "the feeling in my leg" are perfectly good values.
_VACUOUS = frozenset(["feel", "feeling", "thing", "stuff"])


def is_vacuous(value: str) -> bool:
    """Whether `value` is made up entirely of contentless placeholder nouns."""
    tokens = _content_tokens(value)
    return bool(tokens) and tokens <= _VACUOUS


def _mint(board: Board, cat: str, value: str) -> str | None:
    """Ensure `value` exists in `cat` (at 0.0 if new); return its canonical key."""
    value = " ".join(value.split())
    if not value or cat not in board:
        return None
    if is_vacuous(value) and _find(board[cat], value) is None:
        return None
    bucket = board[cat]
    key = _find(bucket, value)
    if key is None:
        bucket[value] = 0.0
        key = value
    return key


def update(board: Board, slots: Mapping[str, str], answer: str) -> Board:
    """Add points to the asserted (category, value) pairs; return a new board.

    No normalization. "not_sure" carries no information. Unknown values are
    minted first (so a "no" to a fresh value records it NEGATIVE — it stays
    known-rejected instead of vanishing).

    Crediting is ASYMMETRIC: a yes/kinda credits every asserted pair, but a
    "no" subtracts only from the LOWEST-scoring asserted pair(s) — the
    marginal guess, not the established anchor. ("Do you want to remind Rob
    about your blood pressure?" → no disconfirms *blood pressure*, not the
    already-confirmed *Rob*; in one trial 29 such collateral hits buried the
    round's only confirmed anchor.) A solely-tagged pair still takes the hit,
    so a direct "Is it about Rob?" → no counts in full.
    """
    out = copy_board(board)
    delta = {"yes": YES_POINTS, "kinda": KINDA_POINTS, "no": -NO_POINTS}.get(answer)
    if delta is None:
        return out
    minted: list[tuple[str, str]] = []
    for cat, value in slots.items():
        if not isinstance(value, str) or not value.strip():
            continue
        key = _mint(out, cat, value)
        if key is not None:
            minted.append((cat, key))
    if not minted:
        return out
    if delta < 0 and len(minted) > 1:
        low = min(out[cat][key] for cat, key in minted)
        targets = [(cat, key) for cat, key in minted if out[cat][key] == low]
    else:
        targets = minted
    for cat, key in targets:
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


# ----------------------------------------- refinement links (coarse → fine)

#: Per-category child→parent edges: "tingling" refines "discomfort". Derived
#: deterministically from the board + history tags (replay-safe); edges shape
#: READING only — scores stay flat and text-anchored (the Aaron rule).
Edges = dict[str, dict[str, str]]


def empty_edges() -> Edges:
    return {cat: {} for cat in CATEGORIES}


def _is_ancestor(cat_edges: Mapping[str, str], node: str, of: str) -> bool:
    """Whether `node` sits on `of`'s ancestor chain (cycle guard)."""
    cur = of
    for _ in range(12):
        if cur not in cat_edges:
            return False
        cur = cat_edges[cur]
        if cur == node:
            return True
    return False


def derive_edges(board: Board, tags: list[tuple[str, str, str]]) -> Edges:
    """Child→parent edges from explicit tags + a lexical-subset fallback.

    Explicit ``(cat, child, parent)`` tags (history order, first one wins)
    attach only when BOTH values already exist on the board — a tag can link
    contenders, never resurrect or invent them. The fallback links an
    untagged value to the largest OLDER value whose content tokens it wholly
    contains ("upper thigh" ⊃ "thigh"; "a specific cleanup task" ⊃ "a task")
    — insertion order makes that acyclic by construction; synonym
    refinements (tingling → discomfort) need the explicit tag.
    """
    edges = empty_edges()
    for cat, child, parent in tags:
        if cat not in board:
            continue
        ck = _find(board[cat], child)
        pk = _find(board[cat], parent)
        if ck is None or pk is None or ck == pk or ck in edges[cat]:
            continue
        if _is_ancestor(edges[cat], ck, pk):
            continue  # would cycle
        edges[cat][ck] = pk
    for cat in CATEGORIES:
        keys = list(board.get(cat, {}))
        for i, child in enumerate(keys):
            if child in edges[cat]:
                continue
            ctok = _content_tokens(child)
            if not ctok:
                continue
            best: str | None = None
            best_size = 0
            for parent in keys[:i]:  # only OLDER values can parent
                ptok = _content_tokens(parent)
                if not ptok or len(ptok) <= best_size:
                    continue
                if value_extends(child, parent):
                    best, best_size = parent, len(ptok)
            if best is not None and not _is_ancestor(edges[cat], child, best):
                edges[cat][child] = best
    return edges


def value_extends(child: str, parent: str) -> bool:
    """Whether `child` lexically EXTENDS `parent` ("Avalanche tickets" ⊃
    "tickets") — the refine-vs-replace decision for caregiver edits and the
    lexical edge fallback."""
    ctok = _content_tokens(child)
    ptok = _content_tokens(parent)
    if not ctok or not ptok or len(ptok) >= len(ctok):
        return False
    return all(any(_tokens_match(p, c) for c in ctok) for p in ptok)


def family_root(cat_edges: Mapping[str, str], value: str) -> str:
    cur = value
    for _ in range(12):
        if cur not in cat_edges:
            return cur
        cur = cat_edges[cur]
    return cur


def _depth(cat_edges: Mapping[str, str], value: str) -> int:
    d = 0
    cur = value
    while cur in cat_edges and d < 12:
        cur = cat_edges[cur]
        d += 1
    return d


def families(board: Board, cat: str, edges: Edges) -> list[tuple[str, float]]:
    """(root, family mass) per LIVE family, highest mass first.

    Family mass sums the NON-NEGATIVE member scores: a child's yes lifts the
    family (the implicit upward credit), a child's no hits only that child —
    failed fine guesses never erode the family's standing, so an established
    parent stays locked while the round weaves through its children. (The
    owner's "functional protection", delivered at read time instead of by
    propagated points — propagation would write credit onto values the
    question never said, the Aaron-bug class.)
    """
    cat_edges = edges.get(cat, {})
    masses: dict[str, float] = {}
    alive: set[str] = set()
    for value, score in board.get(cat, {}).items():
        root = family_root(cat_edges, value)
        masses[root] = masses.get(root, 0.0) + max(0.0, score)
        if score > ELIMINATE_FLOOR:
            alive.add(root)
    ranked = [(r, m) for r, m in masses.items() if r in alive]
    ranked.sort(key=lambda t: t[1], reverse=True)
    return ranked


def family_confident(
    board: Board, cat: str, edges: Edges, *, ready_points: float, margin: float
) -> bool:
    """`confident`, read at family level (mass + lead over the rival family)."""
    ranked = families(board, cat, edges)
    if not ranked or ranked[0][1] < ready_points:
        return False
    if len(ranked) == 1:
        return True
    return (ranked[0][1] - ranked[1][1]) >= margin


def frontier(
    board: Board, cat: str, edges: Edges, *, min_own: float = YES_POINTS
) -> tuple[str, float] | None:
    """The weave value: the leading family's deepest member with own score
    ≥ ``min_own`` (one confirmed yes — verify-on-lock covers thin locks).

    Ties prefer the higher own score. When no member is confirmed at
    ``min_own`` the best positive member stands in (often the root), and if
    a once-confirmed child later loses support the frontier RETREATS to its
    parent — the draft coarsens honestly instead of clinging to a value the
    answers no longer back.
    """
    ranked = families(board, cat, edges)
    if not ranked or ranked[0][1] <= 0:
        return None
    cat_edges = edges.get(cat, {})
    root = ranked[0][0]
    members = [
        (v, s)
        for v, s in board.get(cat, {}).items()
        if s > ELIMINATE_FLOOR and family_root(cat_edges, v) == root
    ]
    qualified = [(v, s) for v, s in members if s >= min_own]
    if qualified:
        return max(qualified, key=lambda t: (_depth(cat_edges, t[0]), t[1]))
    positive = [(v, s) for v, s in members if s > 0]
    return max(positive, key=lambda t: t[1]) if positive else None


def chain(cat_edges: Mapping[str, str], value: str) -> list[str]:
    """Root-first ancestry path ending at `value` (for rendering `a › b › c`)."""
    path = [value]
    cur = value
    for _ in range(12):
        if cur not in cat_edges:
            break
        cur = cat_edges[cur]
        path.append(cur)
    path.reverse()
    return path


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
            token = token[: -len(suf)]
            break
    # "worried" -> "worri" -> "worry": without the y-restore, a stemmed
    # variant never matches its own base form and the two fragment the slot
    # ("worried" +1.0 beside "worry" -0.5 in the 08-31 round).
    return f"{token[:-1]}y" if token.endswith("i") else token


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


def head_token(value: str) -> str | None:
    """The value's head — its LAST content token.

    English noun phrases are head-final ("right thigh" → thigh, "a specific
    cleanup task" → task), so the head is what the value is fundamentally
    *about*; the tokens before it are modifiers. Two values that disagree on
    the head are different things, however many modifiers they share.
    """
    tokens = [
        _stem(t)
        for t in _TOKEN_RE.findall(value.casefold())
        if len(t) >= 3 and t not in _STOPWORDS
    ]
    return tokens[-1] if tokens else None


def mentions(text: str, value: str) -> bool:
    """Whether `text` actually says `value` — the crediting anchor.

    A single-token value matches when that token appears (stemmed,
    prefix-tolerant); a value with no content tokens (e.g. "help") falls back
    to a substring check.

    A MULTI-token value additionally requires its :func:`head_token` to appear
    and at least half its tokens to match. Plain `any()` was far too loose:
    "Is the pain you are feeling happening **right** now?" credited
    `where: right side` off the word "right", and "Are you **feeling**
    lonely?" put −1.0 on `why: feeling overwhelmed`. Both are real 08-31/09-01
    trial defects — a hole in the very gate built to stop score drift.
    """
    vtok = _content_tokens(value)
    if not vtok:
        return value.casefold().strip() in text.casefold()
    ttok = _content_tokens(text)
    if len(vtok) == 1:
        return any(_tokens_match(v, t) for v in vtok for t in ttok)
    head = head_token(value)
    if head is None or not any(_tokens_match(head, t) for t in ttok):
        return False
    return overlap_ratio(value, text) >= 0.5


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

    Folds rewordings together ("the picture" → "a picture") so points accrue to
    one contender instead of fragmenting. Returns `value` unchanged when
    nothing matches. Two guards keep it from folding things that are NOT the
    same — both are 09-01 trial defects:

    * **Never fold across a refinement.** If either value lexically extends the
      other ("a phone call" ⊃ "a call"), they are parent and child, and folding
      them collapses the very structure W3-H exists to drill through.
    * **Multi-token values must agree on their head.** Raw overlap ≥ 0.5 meant
      any two-token values sharing one token collapsed, so "right leg",
      "right thigh", "right arm" and "right calf" all *became* "right side" on
      the shared modifier "right". The board could then never get more specific
      than its seeds, and — because the folded child then equalled the parent
      named in the model's ``refines`` tag — every refinement edge was dropped.
      `board.edges` was empty in all 8 recorded rounds.

    Matching is prefix-tolerant (the same ``_tokens_match`` the anchor uses),
    so morphological variants like "confused"/"confusion" now fold rather than
    sitting on the board as rivals.
    """
    bucket = board.get(cat, {})
    exact = _find(bucket, value)
    if exact is not None:
        return exact
    tokens = _content_tokens(value)
    if not tokens:
        return value
    head = head_token(value)
    best, best_score = None, 0.0
    for existing in bucket:
        etok = _content_tokens(existing)
        if not etok:
            continue
        if value_extends(value, existing) or value_extends(existing, value):
            continue  # a refinement pair — keep them distinct so an edge can form
        if len(tokens) > 1 or len(etok) > 1:
            ehead = head_token(existing)
            if head is None or ehead is None or not _tokens_match(head, ehead):
                continue
        hits = sum(1 for x in tokens if any(_tokens_match(x, y) for y in etok))
        inter = hits / max(len(tokens), len(etok))
        if inter > best_score:
            best, best_score = existing, inter
    if best is not None and best_score >= 0.5:
        return best
    return value


# --------------------------------------------------- slot-category discipline

#: Verbs that mark a value as an ACTION — it belongs in "how", not "what".
#: (One trial filed "help with tasks" under what at +6 while how never
#: established, jamming the focus policy on an unfillable slot.)
_ACTION_VERBS = frozenset(
    [
        "help", "move", "bring", "call", "clean", "fix", "hang", "organize",
        "organise", "visit", "take", "get", "give", "make", "carry", "lift",
        "wash", "cook", "drive", "hold", "open", "close", "turn", "put", "set",
        "show", "tell", "ask", "remind", "warn", "thank", "reassure", "check",
        "buy", "send", "write", "read", "play", "walk", "come", "go", "stay",
        "sit", "talk", "phone", "arrange", "tidy", "rearrange",
    ]
)


def action_like(value: str) -> bool:
    """Whether a slot value reads as an action (verb-led phrase)."""
    tokens = _TOKEN_RE.findall(value.casefold())
    return any(_stem(t) in _ACTION_VERBS or t in _ACTION_VERBS for t in tokens[:2])


def remap_slot(cat: str, value: str) -> str:
    """Re-file a model-tagged pair into the right category (action → how)."""
    if cat == "what" and action_like(value):
        return "how"
    return cat


# ------------------------------------------- the direction layer (sign flip)

#: The four intent buckets for person-topics, as standing "how" contenders.
#: Direction is a binary attribute once the person is fixed, so a "no" on one
#: pole is soft evidence for the OPPOSITE pole (the caregiver's "sign flip").
DIRECTION_BUCKETS: dict[str, str] = {
    "me_for_them": "do something for them",
    "them_for_me": "have them do something for me",
    "tell_them": "tell them something",
    "ask_them": "ask them something",
}
MIRROR: dict[str, str] = {
    "me_for_them": "them_for_me",
    "them_for_me": "me_for_them",
    "tell_them": "ask_them",
    "ask_them": "tell_them",
}

_PRONOUNS = r"him|her|them|he|she|they|someone|somebody|family"
# Verbs whose me→them use means CONVEYING information (bucket: tell_them).
_TELL_VERBS = r"tell|remind|warn|show|thank|reassure"
_ASK_VERBS = r"ask"


def _who_pattern(who_names: list[str]) -> str:
    """Alternation matching any known who-name token (or a person pronoun)."""
    tokens: set[str] = set()
    for name in who_names:
        for t in _TOKEN_RE.findall(name.casefold()):
            if len(t) >= 3 and t not in _STOPWORDS:
                tokens.add(re.escape(t))
    parts = sorted(tokens) + [_PRONOUNS]
    return "|".join(parts)


#: "want / wanting / need / hoping / trying / would like …" — the desire stem.
_WANT = r"(?:want\w*|need\w*|like|hop\w*|try\w*|wish\w*)"
#: State verbs after "<who> to …" that signal a CONCERN, not a task request
#: ("Do you want Rob to be okay?" is care ABOUT them — never direction).
_STATE_VERBS = r"(?:be|feel|seem)"


def classify_direction(question: str, who_names: list[str]) -> str | None:
    """Which intent bucket a question asserts, from its own text — or None.

    Pure code (the model never tags buckets — bucket phrases are stopword-
    heavy, so mention-anchoring can't verify them). Conservative: returns
    None when no pattern clearly matches, and concern-about-the-person
    phrasings ("want Rob to be okay", "does Rob seem…") are deliberately
    left unclassified so genuine concerns never read as task direction.

    - "Do you want to tell Rob …" / "…want Rob to know…"  → tell_them
    - "Do you want to ask Julie about …"                   → ask_them
    - "Do you want/need Rob to clean …", "Will Zach help…" → them_for_me
    - "Do you want to bring Rob a drink?", "…for Rob?"     → me_for_them
    """
    q = " ".join(question.casefold().split())
    who = _who_pattern(who_names)

    # tell/ask are checked first — syntactically a "me → them" act but
    # semantically their own buckets.
    if re.search(rf"\b{_WANT}\s+to\s+(?:{_TELL_VERBS})\b.*\b(?:{who})\b", q) or re.search(
        rf"\b(?:{_TELL_VERBS})\s+(?:your\s+\w+|{who})\b", q
    ):
        return "tell_them"
    if re.search(rf"\b{_WANT}\s+(?:\w+\s+){{0,2}}?(?:{who})\s+to\s+know\b", q):
        return "tell_them"
    if re.search(rf"\b{_WANT}\s+to\s+(?:{_ASK_VERBS})\b.*\b(?:{who})\b", q) or re.search(
        rf"\b(?:{_ASK_VERBS})\s+(?:your\s+\w+|{who})\b", q
    ):
        return "ask_them"
    if re.search(
        rf"\bto\s+know\s+(?:if|when|where|whether|what|how)\b.*\b(?:{who})\b", q
    ):
        return "ask_them"

    # them_for_me: "want/need <who> to <action>", "will/should/can <who> …"
    if re.search(
        rf"\b{_WANT}\s+(?!to\b)(?:\w+\s+){{0,2}}?(?:{who})\s+to\s+(?!{_STATE_VERBS}\b)\w+",
        q,
    ):
        return "them_for_me"
    if re.search(rf"\b(?:will|should|can|could)\s+(?:your\s+\w+|{who})\b", q):
        return "them_for_me"

    # me_for_them: "want to <verb> … <who>", "… for <who>"
    if re.search(rf"\b{_WANT}\s+to\s+\w+\s+(?:\w+\s+){{0,3}}?(?:{who})\b", q):
        return "me_for_them"
    if re.search(rf"\bfor\s+(?:your\s+\w+|{who})\b", q):
        return "me_for_them"
    return None


def facet_view(board: Board, focus: str = "", edges: Edges | None = None) -> list[dict]:
    """The cockpit tile payload: every category with its top live contenders.

    Raw accumulated points (can be 0 / negative for shown leaders' rivals) —
    that is the actual consensus right now, honestly displayed. With `edges`,
    contenders come family-grouped (each family's chain in root→leaf order,
    families by mass) and each contender carries its `parent`, so the tile
    can render the dive: ``discomfort › tingling``.
    """
    view: list[dict] = []
    for cat in CATEGORIES:
        if edges is not None and edges.get(cat):
            cat_edges = edges[cat]
            ranked_live = dict(live(board, cat))
            ordered: list[tuple[str, float]] = []
            for root, _mass in families(board, cat, edges):
                fam = [
                    (v, s)
                    for v, s in ranked_live.items()
                    if family_root(cat_edges, v) == root
                ]
                # Chain order: shallow → deep, higher score first among peers.
                fam.sort(key=lambda t: (_depth(cat_edges, t[0]), -t[1]))
                ordered.extend(fam)
            contenders = [
                {
                    "value": v,
                    "score": round(s, 2),
                    "parent": cat_edges.get(v),
                }
                for v, s in ordered[:MAX_LISTED]
            ]
        else:
            contenders = [
                {"value": v, "score": round(s, 2), "parent": None}
                for v, s in live(board, cat)[:MAX_LISTED]
            ]
        view.append(
            {
                "category": cat,
                "label": CATEGORY_LABEL[cat],
                "contenders": contenders,
                "focus": cat == focus,
            }
        )
    return view
