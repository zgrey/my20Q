"""Smoke test for the CLI harness — confirms it imports and parses args."""

from __future__ import annotations

import pytest

from my20q.cli import main


def test_cli_help_exits_cleanly() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
