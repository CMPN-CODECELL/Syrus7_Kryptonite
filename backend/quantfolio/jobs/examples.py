"""Export a genuine, succeeded run as the saved example (data/examples/<run_id>/).

The API imports it read-only at startup and serves it at /api/example. Only
files the run itself produced are copied; nothing is re-computed or edited.

    python -m quantfolio.jobs.examples --data-dir <dir> --run-id <run_id> [--dest <examples dir>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..settings import get_settings
from .fsutil import atomic_write_bytes, is_valid_run_id
from .store import JobStore

LABEL = "Default example (8 NIFTY assets, K=4, depth-1 QAOA) - genuine local simulator run"


def export_example(db_path: Path, runs_dir: Path, run_id: str, dest_root: Path, label: str = LABEL) -> Path:
    if not is_valid_run_id(run_id):
        raise ValueError("invalid run id")
    row = JobStore(db_path).get(run_id)
    if row is None or row["status"] != "succeeded" or row["cached_from"]:
        raise ValueError("only a freshly computed, succeeded run can be exported")
    src = runs_dir / run_id
    result = json.loads((src / "result.json").read_text(encoding="utf-8"))
    if result.get("run_id") != run_id:
        raise ValueError("result.json does not belong to this run")
    dest = dest_root / run_id
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("result.json", "final_counts.json"):
        if (src / name).is_file():
            atomic_write_bytes(dest / name, (src / name).read_bytes())
    meta = {
        "kind": row["kind"], "label": label, "config": json.loads(row["config_json"]), "config_hash": row["config_hash"],
        "cache_key": row["cache_key"], "created_at": row["created_at"], "finished_at": row["finished_at"],
        "summary": json.loads(row["summary_json"] or "{}"), "evaluations_done": row["evaluations_done"],
        "evaluation_budget": row["evaluation_budget"],
    }
    atomic_write_bytes(dest / "example.json", json.dumps(meta, indent=2, sort_keys=True).encode("utf-8"))
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m quantfolio.jobs.examples")
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--dest", type=Path, default=None)
    a = ap.parse_args(argv)
    dest = a.dest or get_settings().examples_dir
    try:
        out = export_example(a.data_dir / "state" / "quantfolio.sqlite3", a.data_dir / "runs", a.run_id, dest)
    except (ValueError, OSError) as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
