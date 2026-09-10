"""Tests for the noise bench (W2-G) — the parts that need no LLM.

The bench's value is that it turns the §1 autopsy into a script output, so the
pieces that compute the numbers must be correct: the ε-noise wrapper, the
metric extraction, and the run-to-run diff. All three are pure functions over
a round record, which is what lets them be tested without Ollama.

The driver itself (`_drive_round`) needs two live models and is exercised by
running the bench, not by pytest.
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

from my20q.agent.dialogue import Answer

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_reasoning.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("bench_reasoning", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: the script uses `from __future__ import
    # annotations`, so @dataclass resolves its string annotations through
    # sys.modules[cls.__module__] and would fail on an unregistered module.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    yield module
    sys.modules.pop(spec.name, None)


# ------------------------------------------------------------ the ε-noise


def test_zero_noise_is_the_identity(bench) -> None:
    rng = random.Random(0)
    for answer in Answer:
        assert bench.apply_noise(answer, 0.0, rng) is answer


def test_noise_only_flips_decisive_answers(bench) -> None:
    # A hedge is not a wrong decisive answer — the Rényi–Ulam model this comes
    # from noises the decisive channel only. ε=1.0 forces every eligible flip.
    rng = random.Random(0)
    assert bench.apply_noise(Answer.YES, 1.0, rng) is Answer.NO
    assert bench.apply_noise(Answer.NO, 1.0, rng) is Answer.YES
    assert bench.apply_noise(Answer.KINDA, 1.0, rng) is Answer.KINDA
    assert bench.apply_noise(Answer.NOT_SURE, 1.0, rng) is Answer.NOT_SURE


def test_noise_rate_is_roughly_epsilon(bench) -> None:
    rng = random.Random(1234)
    flips = sum(
        bench.apply_noise(Answer.YES, 0.2, rng) is Answer.NO for _ in range(4000)
    )
    assert 0.17 < flips / 4000 < 0.23


def test_noise_is_reproducible_across_processes(bench) -> None:
    # The per-scenario streams are seeded from a STRING so random.seed hashes
    # it with sha512 — a tuple's hash is randomized by PYTHONHASHSEED and would
    # silently make every --compare delta a different coin sequence.
    def draw() -> list[Answer]:
        rng = random.Random("7:leg-laterality:0.1:3")
        return [bench.apply_noise(Answer.YES, 0.1, rng) for _ in range(50)]

    assert draw() == draw()
    assert draw() != [Answer.YES] * 50  # it really is flipping some


# ------------------------------------------------------------ the metrics


def _record(**over) -> dict:
    """A W2-O-era round record, the shape build_round_record emits."""
    base = {
        "outcome": "synthesized",
        "job_b": 9.5,
        "queries": [
            {"kind": "query", "text": "q1", "answer": "no", "focus": "what",
             "banner": {"ready": False, "text": "", "parts": []},
             "timing": {"total_ms": 5000.0, "llm_calls": 2, "attempts": 1}},
            {"kind": "query", "text": "q2", "answer": "yes", "focus": "what",
             "informative": True,
             "banner": {"ready": False, "text": "a drink", "parts": [1]},
             "rejections": ["you already asked that"],
             "timing": {"total_ms": 11000.0, "llm_calls": 4, "attempts": 2}},
            {"kind": "diagnostic", "text": "boom", "answer": None},
            {"kind": "query", "text": "q3", "answer": "yes", "focus": "where",
             "informative": False, "verify": True,
             "banner": {"ready": True, "text": "a drink", "parts": [1]},
             "timing": {"total_ms": 6000.0, "llm_calls": 2, "attempts": 1}},
            {"kind": "query", "text": "q4", "answer": "yes", "focus": "where",
             "informative": True,
             "banner": {"ready": True, "text": "a cold drink", "parts": [1]},
             "timing": {"total_ms": 4000.0, "llm_calls": 2, "attempts": 1}},
        ],
        "board": {"restarts": [{"reason": "no-streak", "after_query": 2}]},
        "seed_ms": 1400.0,
    }
    base.update(over)
    return base


def test_round_metrics_reads_the_autopsy_numbers(bench) -> None:
    m = bench.round_metrics(_record())
    assert m["converged"] is True
    assert m["queries"] == 4
    assert m["answers"] == {"no": 1, "yes": 3}
    assert m["focus"] == {"what": 2, "where": 2}
    assert m["informative_yes"] == 2 and m["farming_yes"] == 1
    assert m["restarts"] == 1 and m["restart_positions"] == [2]
    assert m["diagnostics"] == 1
    assert m["verifies"] == 1
    assert m["gate_rejections"] == 1 and m["questions_re_asked"] == 1
    assert m["seed_ms"] == 1400.0
    assert m["latency"]["max_ms"] == 11000.0


def test_ready_transition_is_the_wasted_query_estimate(bench) -> None:
    # The metric F2 exists to report: the draft became offerable at q3, so the
    # round spent one further question past the point where proposing was the
    # better move.
    m = bench.round_metrics(_record())
    assert m["ready_at_query"] == 3
    assert m["queries_after_ready"] == 1


def test_metrics_degrade_gracefully_on_a_pre_w2o_record(bench) -> None:
    # An archived recording carries no banner/timing/rejections. It must still
    # produce the metrics that do not depend on them, not raise.
    legacy = _record(
        queries=[{"kind": "query", "text": "q1", "answer": "yes", "focus": "what"}],
        board={"restarts": [{"reason": "stalled"}]},  # no after_query
    )
    legacy.pop("seed_ms")
    m = bench.round_metrics(legacy)
    assert m["queries"] == 1
    assert m["restarts"] == 1
    assert m["restart_positions"] == []  # position unknowable pre-W2-O
    assert m["ready_at_query"] is None
    assert m["queries_after_ready"] is None
    assert m["latency"] == {}
    assert m["seed_ms"] is None


def test_aggregate_averages_queries_over_converged_rounds_only(bench) -> None:
    # A round that hit the cap would otherwise drag the mean toward the cap,
    # which measures the cap rather than the engine.
    won = bench.round_metrics(_record())
    lost = bench.round_metrics(_record(outcome="abandoned"))
    agg = bench.aggregate_metrics([won, lost])
    assert agg["rounds"] == 2 and agg["converged"] == 1
    assert agg["queries_to_converge"] == 4.0  # the abandoned round excluded
    assert agg["informative_yes_ratio"] == round(4 / 6, 3)  # both rounds counted


def test_aggregate_of_nothing_is_empty(bench) -> None:
    assert bench.aggregate_metrics([]) == {}


# ------------------------------------------------------------ the compare


def _report(model: str, noise: float, records: list[dict], bench) -> dict:
    return {
        "model": model,
        "noise": noise,
        "rounds": [{"metrics": bench.round_metrics(r)} for r in records],
    }


def test_compare_diffs_matching_runs(bench) -> None:
    before = [_report("gemma4:e4b", 0.0, [_record(), _record(outcome="abandoned")], bench)]
    after = [_report("gemma4:e4b", 0.0, [_record(), _record()], bench)]
    diff = bench.compare_runs(before, after)
    assert len(diff) == 1
    entry = diff[0]
    assert entry["run"] == "gemma4:e4b  ε=0"
    assert entry["metrics"]["converged"]["delta"] == 1  # 1 -> 2
    assert entry["metrics"]["queries_to_converge"]["delta"] == 0


def test_compare_keeps_a_run_that_exists_on_one_side_only(bench) -> None:
    # A run that stopped happening is a result, not something to drop silently.
    before = [_report("gemma4:e4b", 0.0, [_record()], bench)]
    after = [_report("gemma3:12b", 0.0, [_record()], bench)]
    runs = {e["run"] for e in bench.compare_runs(before, after)}
    assert runs == {"gemma4:e4b  ε=0", "gemma3:12b  ε=0"}


def test_compare_matches_on_noise_level_too(bench) -> None:
    before = [
        _report("gemma4:e4b", 0.0, [_record()], bench),
        _report("gemma4:e4b", 0.2, [_record(outcome="abandoned")], bench),
    ]
    after = [
        _report("gemma4:e4b", 0.0, [_record()], bench),
        _report("gemma4:e4b", 0.2, [_record()], bench),
    ]
    by_run = {e["run"]: e for e in bench.compare_runs(before, after)}
    assert by_run["gemma4:e4b  ε=0"]["metrics"]["converged"]["delta"] == 0
    assert by_run["gemma4:e4b  ε=0.2"]["metrics"]["converged"]["delta"] == 1


# ------------------------------------------------------------ the CLI


def test_compare_mode_runs_without_touching_ollama(bench, tmp_path, capsys) -> None:
    before = tmp_path / "before.json"
    after = tmp_path / "after.json"
    before.write_text(
        json.dumps([_report("gemma4:e4b", 0.0, [_record(outcome="abandoned")], bench)]),
        encoding="utf-8",
    )
    after.write_text(
        json.dumps([_report("gemma4:e4b", 0.0, [_record()], bench)]), encoding="utf-8"
    )
    assert bench.main(["--compare", str(before), str(after)]) == 0
    assert "gemma4:e4b" in capsys.readouterr().out


def test_noise_outside_a_probability_is_rejected(bench) -> None:
    with pytest.raises(SystemExit):
        bench.main(["--noise", "1.5"])


def test_the_regression_fixture_is_synthetic(bench) -> None:
    # The 09-01 round it stands for was a real-patient session; only its SHAPE
    # (a side, then a limb, then a part of that limb) may be committed.
    scenario = next(s for s in bench.SCENARIOS if s.name == "leg-laterality")
    assert scenario.topic_id == "physical_health"
    assert "right" in scenario.need and "left" in scenario.need


def test_readiness_metrics_are_not_conditioned_on_convergence(bench) -> None:
    # Reaching readiness is the ENGINE's milestone — the point where the draft
    # becomes a woven sentence instead of the code template — and it happens in
    # rounds the caregiver never accepts. Scoping it to converged rounds made a
    # run read "—" for both, which would hide exactly the effect the W2-R fix
    # was measured for.
    ready_but_lost = bench.round_metrics(_record(outcome="abandoned"))
    assert ready_but_lost["converged"] is False
    assert ready_but_lost["ready_at_query"] == 3

    agg = bench.aggregate_metrics([ready_but_lost])
    assert agg["converged"] == 0
    assert agg["reached_ready"] == 1        # counted despite not converging
    assert agg["ready_at_query"] == 3.0
    assert agg["queries_after_ready"] == 1.0
    assert agg["queries_to_converge"] is None  # this one IS conditional


def test_the_simulator_never_runs_with_thinking_on(bench) -> None:
    # Load-bearing, not tidiness. The simulator's calls are tiny (max_tokens
    # 4-8, one word expected) and gemma4 is a THINKING model: with thinking on
    # the budget is spent before any content is emitted, and OllamaBackend
    # salvages the chain-of-thought instead ("Thinking Process: 1. **Analyze
    # the Goal:** ..."). _simulate_confirm checks startswith("y"), so it
    # returned False for EVERY draft and the bench's `converged` column was
    # structurally zero in every run to date.
    sim = bench._make_simulator("gemma4:e4b")
    assert sim.think is False


def test_the_confirm_oracle_judges_by_meaning(bench) -> None:
    # It stands in for the caregiver pressing ✓, so its prompt must ask the
    # question a caregiver actually asks — "would this get me what I need?" —
    # not "does this sentence correctly capture it", which invited pedantry and
    # rejected a draft the real caregiver would have accepted.
    p = bench._SIM_CONFIRM
    assert "content to have that said for you" in p
    assert "leaves out a detail" in p  # paraphrase is acceptable
    # It must still name the failures that MUST be rejected.
    for wrong in ("wrong thing", "wrong person", "wrong side of the body"):
        assert wrong in p
