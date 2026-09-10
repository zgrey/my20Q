"""Prune demo rounds from a recorded dataset, and archive older sessions.

The tool gets opened to check it still works far more often than it gets used
for a real round, and every one of those leaves a round in the dataset: opened,
seeded, closed, nothing answered. They are indistinguishable from signal in a
file listing and they crowd the cockpit's session picker, so this removes them.

  VACUOUS  = a round with no answered query and no entries of any kind.
             `emergency` rounds are EXEMPT: the emergency topic short-circuits
             the LLM by design, so zero queries is correct there and the record
             is the only evidence that safety path was exercised.
  ARCHIVE  = moved to <patient>/archive/. The cockpit's picker and its session
             review both read the patient directory's top level only, so an
             archived session leaves the UI while staying intact on disk and
             readable with dump_recording.py. Its bytes also stop counting
             toward the dataset-size warning.

This is real patient data under the privacy invariant: local-only, and nothing
here moves a file outside the patient directory. A full backup is taken before
any change unless you pass --no-backup, and rounds that are KEPT are written
back as their original bytes — never re-serialised — so a surviving round is
never altered, not even its JSON formatting.

Nothing is written without --apply.

Usage:
  python scripts/prune_recordings.py                      # dry run, profile's dir
  python scripts/prune_recordings.py --apply
  python scripts/prune_recordings.py --archive-before 2026-07 --apply
  python scripts/prune_recordings.py patient_data/Paula --apply
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path

# Recordings carry unicode; the legacy Windows console encodes cp1252 and would
# crash mid-report — force UTF-8 on the streams (same guard as the dumper).
for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def is_vacuous(record: dict) -> bool:
    """A round that recorded nothing — the tool was opened, not used.

    The emergency short-circuit is exempt by outcome, not by topic id, so a
    renamed or descendant emergency topic is still protected.
    """
    if record.get("outcome") == "emergency":
        return False
    entries = record.get("queries", [])
    if any(e.get("kind") == "query" and e.get("answer") for e in entries):
        return False
    return not entries


def _month(record: dict) -> str:
    """The YYYY-MM a round was recorded in ("" when the field is missing)."""
    return str(record.get("recorded_at", ""))[:7]


def default_patient_dir() -> Path | None:
    """`recording_dir / profile.id`, matching how the API builds the Recorder.

    Returns None when no profile is configured — development territory, where
    there is no recorded dataset to prune.
    """
    try:
        from my20q.config import Config
        from my20q.profiles import load_profile
    except ImportError:  # pragma: no cover - only when run outside the venv
        return None
    config = Config.from_env()
    profile = load_profile(config.profile_path)
    if profile is None:
        return None
    return Path(config.recording_dir) / profile.id


def plan(patient_dir: Path, archive_before: str = "") -> dict:
    """Decide what to delete / prune / archive. Pure — touches nothing."""
    delete: list[tuple[str, int]] = []
    prune: list[tuple[str, int]] = []
    archive: list[str] = []
    rewritten: dict[Path, list[str]] = {}
    kept_rounds = 0

    for path in sorted(patient_dir.glob("*.jsonl")):
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        keep: list[str] = []
        months: list[str] = []
        dropped = 0
        for line in lines:
            record = json.loads(line)
            months.append(_month(record))
            if is_vacuous(record):
                dropped += 1
            else:
                keep.append(line)  # ORIGINAL bytes

        if not keep:
            delete.append((path.name, len(lines)))
            continue
        if dropped:
            prune.append((path.name, dropped))
            rewritten[path] = keep
        kept_rounds += len(keep)
        # Archive only when EVERY round in the file predates the cutoff, so a
        # session straddling it stays live.
        if archive_before and months and all(m and m < archive_before for m in months):
            archive.append(path.name)

    return {
        "delete": delete,
        "prune": prune,
        "archive": archive,
        "rewritten": rewritten,
        "kept_rounds": kept_rounds,
        "files": [p.name for p in sorted(patient_dir.glob("*.jsonl"))],
    }


def apply_plan(patient_dir: Path, result: dict, *, backup: bool = True) -> Path | None:
    """Execute a plan. Backup first, then rewrite, delete, and move."""
    backup_dir: Path | None = None
    if backup:
        stamp = _dt.datetime.now().strftime("%Y-%m-%dT%H%M%S")
        backup_dir = patient_dir / f".backup-{stamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(patient_dir.glob("*.jsonl")):
            shutil.copy2(path, backup_dir / path.name)

    for path, keep in result["rewritten"].items():
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    for name, _ in result["delete"]:
        (patient_dir / name).unlink(missing_ok=True)
    if result["archive"]:
        archive_dir = patient_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        for name in result["archive"]:
            shutil.move(str(patient_dir / name), str(archive_dir / name))
    return backup_dir


def report(result: dict, *, applied: bool) -> None:
    verb = "" if applied else "would be "
    delete, prune, archive = result["delete"], result["prune"], result["archive"]

    print(f"{verb}deleted — every round vacuous ({len(delete)} file(s)):")
    for name, n in delete or []:
        print(f"    {name[:8]}…  {n} round(s)")
    if not delete:
        print("    (none)")

    total_pruned = sum(n for _, n in prune)
    print(f"\n{verb}pruned — {total_pruned} vacuous round(s) from {len(prune)} file(s):")
    for name, n in prune or []:
        print(f"    {name[:8]}…  −{n}")
    if not prune:
        print("    (none)")

    print(f"\n{verb}archived — {len(archive)} session(s) moved out of the picker")

    live = [
        n
        for n in result["files"]
        if n not in {d[0] for d in delete} and n not in archive
    ]
    print(f"\nrounds remaining: {result['kept_rounds']}")
    print(f"live in the cockpit picker: {len(live)}")
    for name in live:
        print(f"    {name[:8]}…")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prune_recordings", description=__doc__)
    parser.add_argument(
        "patient_dir",
        nargs="?",
        type=Path,
        help="The patient's recording directory (default: the configured "
        "profile's, i.e. MY20Q_DATA_DIR/<profile id>).",
    )
    parser.add_argument(
        "--archive-before",
        default="",
        metavar="YYYY-MM",
        help="Move sessions whose rounds ALL predate this month into "
        "archive/. Omit to archive nothing.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Actually write. Default is a dry run."
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the pre-change backup. Not advised on real patient data.",
    )
    args = parser.parse_args(argv)

    patient_dir = args.patient_dir or default_patient_dir()
    if patient_dir is None:
        parser.error(
            "no patient directory given and no profile configured — pass a "
            "directory, or set MY20Q_PROFILE (scripts/serve_cerberus.sh does)."
        )
    if not patient_dir.is_dir():
        parser.error(f"not a directory: {patient_dir}")
    if args.archive_before and len(args.archive_before) != 7:
        parser.error("--archive-before takes YYYY-MM")

    print(f"patient directory: {patient_dir}\n")
    result = plan(patient_dir, args.archive_before)
    if not result["files"]:
        print("no session files found — nothing to do.")
        return 0

    if args.apply:
        backup_dir = apply_plan(patient_dir, result, backup=not args.no_backup)
        if backup_dir is not None:
            print(f"backed up {len(result['files'])} file(s) -> {backup_dir}\n")
    report(result, applied=args.apply)
    if not args.apply:
        print("\n(dry run — re-run with --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
