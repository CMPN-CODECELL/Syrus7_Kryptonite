"""SQLite (WAL) job store: durable job metadata, events, idempotency keys and
the results index.

State machine (enforced with conditional UPDATEs, so concurrent writers can
never move a job out of a terminal state or apply a transition twice):

    queued  -> running | cancelled
    running -> succeeded | failed | cancelled | timed_out

Connections are per-thread (`sqlite3` objects are not shared between threads)
and run in autocommit mode; multi-statement operations use BEGIN IMMEDIATE so
writers serialise on the database lock instead of failing mid-transaction.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .fsutil import dumps

TERMINAL_STATES = ("succeeded", "failed", "cancelled", "timed_out")
ACTIVE_STATES = ("queued", "running")
ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "queued": ("running", "cancelled"),
    "running": TERMINAL_STATES,
}
# Event types that are never pruned by retention (a client reconnecting late
# must still see stage changes and the terminal outcome).
_KEEP_TYPES = ("status", "stage", "result", "error")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('run', 'experiment')),
    status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed','cancelled','timed_out')),
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    config_json TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    cache_key TEXT,
    idempotency_key TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    timeout_s INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT 'queued',
    evaluations_done INTEGER NOT NULL DEFAULT 0,
    evaluation_budget INTEGER,
    progress_message TEXT,
    error_code TEXT,
    error_message TEXT,
    cached_from TEXT,
    cached_original_time TEXT,
    label TEXT,
    is_example INTEGER NOT NULL DEFAULT 0,
    worker_pid INTEGER,
    attempts INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT,
    result_path TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, seq);
CREATE INDEX IF NOT EXISTS jobs_cache ON jobs(cache_key, status);
CREATE INDEX IF NOT EXISTS jobs_hash ON jobs(config_hash, status);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    stage TEXT,
    data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run ON events(run_id, id);

CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    pid INTEGER,
    started_at TEXT,
    beat_at TEXT,
    current_run TEXT,
    child_pid INTEGER,
    info_json TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS ibm_jobs (
    ibm_job_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    backend TEXT NOT NULL,
    shots INTEGER NOT NULL,
    submitted_at TEXT NOT NULL,
    status TEXT,
    detail TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utcnow()).isoformat(timespec="milliseconds")


def new_run_id(kind: str) -> str:
    prefix = "exp" if kind == "experiment" else "run"
    return f"{prefix}-{utcnow():%Y%m%d}-{secrets.token_hex(4)}"


class StoreError(Exception):
    pass


class QueueFull(StoreError):
    def __init__(self, depth: int, capacity: int):
        super().__init__(f"queue full ({depth}/{capacity})")
        self.depth = depth
        self.capacity = capacity


class JobStore:
    def __init__(self, db_path: Path, max_events_per_run: int = 3000):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_events_per_run = max_events_per_run
        self._local = threading.local()
        self._init_lock = threading.Lock()
        with self._init_lock:
            conn = self._conn()
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ #
    # connection management
    # ------------------------------------------------------------------ #
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE transaction (takes the write lock up front)."""
        conn = self._conn()
        for attempt in range(50):
            try:
                conn.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as exc:  # busy beyond busy_timeout
                if "locked" not in str(exc) or attempt == 49:
                    raise
                time.sleep(0.02)
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------------ #
    # jobs
    # ------------------------------------------------------------------ #
    def active_count(self) -> int:
        row = self._conn().execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()
        return int(row[0])

    def create_job(
        self,
        *,
        kind: str,
        config: dict[str, Any],
        config_hash: str,
        cache_key: str | None,
        timeout_s: int,
        capacity: int,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
        evaluation_budget: int | None = None,
        label: str | None = None,
        summary: dict[str, Any] | None = None,
        dedupe_active: bool = True,
    ) -> tuple[sqlite3.Row, bool]:
        """Insert a queued job. Returns (job_row, created).

        Within one write transaction: an existing idempotency key returns its
        job; an identical active job (same kind + config hash) is reused; a full
        queue raises QueueFull. Nothing is inserted in those cases.
        """
        now = iso()
        with self.tx() as conn:
            if idempotency_key:
                row = conn.execute("SELECT run_id, request_hash FROM idempotency WHERE key=?", (idempotency_key,)).fetchone()
                if row is not None:
                    if request_hash is not None and row["request_hash"] != request_hash:
                        raise StoreError("idempotency_key_reused")
                    job = conn.execute("SELECT * FROM jobs WHERE run_id=?", (row["run_id"],)).fetchone()
                    if job is not None:
                        return job, False
            if dedupe_active:
                job = conn.execute(
                    "SELECT * FROM jobs WHERE kind=? AND config_hash=? AND status IN ('queued','running') ORDER BY seq LIMIT 1",
                    (kind, config_hash),
                ).fetchone()
                if job is not None:
                    if idempotency_key:
                        conn.execute(
                            "INSERT OR IGNORE INTO idempotency(key, run_id, request_hash, created_at) VALUES (?,?,?,?)",
                            (idempotency_key, job["run_id"], request_hash or config_hash, now),
                        )
                    return job, False
            depth = int(conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0])
            if depth >= capacity:
                raise QueueFull(depth, capacity)
            run_id = new_run_id(kind)
            conn.execute(
                """INSERT INTO jobs(run_id, kind, status, created_at, config_json, config_hash, cache_key,
                       idempotency_key, timeout_s, evaluation_budget, label, summary_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    kind,
                    "queued",
                    now,
                    dumps(config),
                    config_hash,
                    cache_key,
                    idempotency_key,
                    int(timeout_s),
                    evaluation_budget,
                    label,
                    dumps(summary or {}),
                ),
            )
            if idempotency_key:
                conn.execute(
                    "INSERT INTO idempotency(key, run_id, request_hash, created_at) VALUES (?,?,?,?)",
                    (idempotency_key, run_id, request_hash or config_hash, now),
                )
            self._insert_event(conn, run_id, "stage", "queued", {"stage": "queued", "message": "Accepted and queued"})
            job = conn.execute("SELECT * FROM jobs WHERE run_id=?", (run_id,)).fetchone()
            self._insert_event(conn, run_id, "status", "queued", self.status_dict(job, conn=conn))
            return job, True

    def create_cached_replay(
        self,
        *,
        source: sqlite3.Row,
        config: dict[str, Any],
        config_hash: str,
        cache_key: str,
        idempotency_key: str | None,
        request_hash: str | None,
    ) -> sqlite3.Row:
        """Record a replay of a stored result: a new history entry, already
        succeeded, pointing at the original. Never counted as a quantum run."""
        now = iso()
        run_id = new_run_id(source["kind"])
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO jobs(run_id, kind, status, created_at, started_at, finished_at, config_json, config_hash,
                       cache_key, idempotency_key, timeout_s, stage, cached_from, cached_original_time, label,
                       summary_json, result_path, evaluations_done, evaluation_budget)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    source["kind"],
                    "succeeded",
                    now,
                    now,
                    now,
                    dumps(config),
                    config_hash,
                    cache_key,
                    idempotency_key,
                    source["timeout_s"],
                    "done",
                    source["run_id"],
                    source["finished_at"],
                    f"Cached replay of {source['run_id']}",
                    source["summary_json"],
                    source["result_path"],
                    source["evaluations_done"],
                    source["evaluation_budget"],
                ),
            )
            if idempotency_key:
                conn.execute(
                    "INSERT OR IGNORE INTO idempotency(key, run_id, request_hash, created_at) VALUES (?,?,?,?)",
                    (idempotency_key, run_id, request_hash or config_hash, now),
                )
            job = conn.execute("SELECT * FROM jobs WHERE run_id=?", (run_id,)).fetchone()
            self._insert_event(
                conn,
                run_id,
                "stage",
                "done",
                {"stage": "done", "message": f"Replayed stored result of {source['run_id']} (no new computation)"},
            )
            self._insert_event(conn, run_id, "status", "done", self.status_dict(job, conn=conn))
            self._insert_event(conn, run_id, "result", "done", {"run_id": run_id, "cached_from": source["run_id"]})
            return job

    def find_cached(self, kind: str, cache_key: str) -> sqlite3.Row | None:
        return self._conn().execute(
            """SELECT * FROM jobs WHERE kind=? AND cache_key=? AND status='succeeded' AND cached_from IS NULL
               AND result_path IS NOT NULL ORDER BY finished_at DESC LIMIT 1""",
            (kind, cache_key),
        ).fetchone()

    def lookup_idempotency(self, key: str) -> tuple[str, str] | None:
        row = self._conn().execute("SELECT run_id, request_hash FROM idempotency WHERE key=?", (key,)).fetchone()
        return None if row is None else (row["run_id"], row["request_hash"])

    def find_active_duplicate(self, kind: str, config_hash: str) -> sqlite3.Row | None:
        return self._conn().execute(
            "SELECT * FROM jobs WHERE kind=? AND config_hash=? AND status IN ('queued','running') ORDER BY seq LIMIT 1",
            (kind, config_hash),
        ).fetchone()

    def counts_by_status(self) -> dict[str, int]:
        rows = self._conn().execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: int(r["n"]) for r in rows}

    def get(self, run_id: str) -> sqlite3.Row | None:
        return self._conn().execute("SELECT * FROM jobs WHERE run_id=?", (run_id,)).fetchone()

    def list_jobs(
        self, *, status: str | None = None, kind: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[sqlite3.Row], int]:
        where, args = [], []
        if status:
            where.append("status=?")
            args.append(status)
        if kind:
            where.append("kind=?")
            args.append(kind)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        conn = self._conn()
        total = int(conn.execute(f"SELECT COUNT(*) FROM jobs {clause}", args).fetchone()[0])
        rows = conn.execute(f"SELECT * FROM jobs {clause} ORDER BY seq DESC LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
        return rows, total

    def queue_position(self, job: sqlite3.Row, conn: sqlite3.Connection | None = None) -> int | None:
        if job["status"] != "queued":
            return None
        c = conn or self._conn()
        ahead = c.execute(
            "SELECT COUNT(*) FROM jobs WHERE status='running' OR (status='queued' AND seq < ?)", (job["seq"],)
        ).fetchone()[0]
        return int(ahead) + 1

    def transition(self, run_id: str, to_state: str, *, expect: tuple[str, ...] | None = None, **fields: Any) -> bool:
        """Guarded state change. Returns True iff this call performed it."""
        sources = expect or tuple(s for s, targets in ALLOWED_TRANSITIONS.items() if to_state in targets)
        for s in sources:
            if to_state not in ALLOWED_TRANSITIONS.get(s, ()):
                raise ValueError(f"illegal transition {s} -> {to_state}")
        sets = ["status=?"]
        args: list[Any] = [to_state]
        if to_state in TERMINAL_STATES:
            fields.setdefault("finished_at", iso())
        for k, v in fields.items():
            sets.append(f"{k}=?")
            args.append(v)
        placeholders = ",".join("?" for _ in sources)
        with self.tx() as conn:
            cur = conn.execute(
                f"UPDATE jobs SET {', '.join(sets)} WHERE run_id=? AND status IN ({placeholders})",
                [*args, run_id, *sources],
            )
            if cur.rowcount != 1:
                return False
            job = conn.execute("SELECT * FROM jobs WHERE run_id=?", (run_id,)).fetchone()
            stage = job["stage"]
            self._insert_event(conn, run_id, "status", stage, self.status_dict(job, conn=conn))
            if to_state == "succeeded":
                self._insert_event(conn, run_id, "result", "done", {"run_id": run_id})
            elif to_state in ("failed", "timed_out"):
                self._insert_event(
                    conn, run_id, "error", stage, {"code": job["error_code"] or to_state, "message": job["error_message"] or to_state}
                )
            return True

    def claim_next(self, worker_pid: int) -> sqlite3.Row | None:
        """Atomically move the oldest queued job to running."""
        with self.tx() as conn:
            row = conn.execute("SELECT run_id FROM jobs WHERE status='queued' ORDER BY seq LIMIT 1").fetchone()
            if row is None:
                return None
            now = iso()
            cur = conn.execute(
                """UPDATE jobs SET status='running', started_at=?, worker_pid=?, attempts=attempts+1,
                       stage='loading_data', progress_message=NULL
                   WHERE run_id=? AND status='queued'""",
                (now, worker_pid, row["run_id"]),
            )
            if cur.rowcount != 1:
                return None
            job = conn.execute("SELECT * FROM jobs WHERE run_id=?", (row["run_id"],)).fetchone()
            self._insert_event(conn, job["run_id"], "status", "loading_data", self.status_dict(job, conn=conn))
            return job

    def request_cancel(self, run_id: str) -> str:
        """Returns one of: 'cancelled' (was queued), 'requested' (running),
        'terminal' (already finished), 'missing'."""
        with self.tx() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE run_id=?", (run_id,)).fetchone()
            if job is None:
                return "missing"
            if job["status"] == "queued":
                conn.execute(
                    "UPDATE jobs SET status='cancelled', finished_at=?, cancel_requested=1, progress_message=? WHERE run_id=? AND status='queued'",
                    (iso(), "Cancelled before start", run_id),
                )
                job = conn.execute("SELECT * FROM jobs WHERE run_id=?", (run_id,)).fetchone()
                self._insert_event(conn, run_id, "status", job["stage"], self.status_dict(job, conn=conn))
                return "cancelled"
            if job["status"] == "running":
                if not job["cancel_requested"]:
                    conn.execute("UPDATE jobs SET cancel_requested=1 WHERE run_id=? AND status='running'", (run_id,))
                    self._insert_event(conn, run_id, "log", job["stage"], {"level": "info", "message": "Cancellation requested"})
                return "requested"
            return "terminal"

    def is_cancel_requested(self, run_id: str) -> bool:
        row = self._conn().execute("SELECT cancel_requested, status FROM jobs WHERE run_id=?", (run_id,)).fetchone()
        return bool(row is None or row["cancel_requested"] or row["status"] != "running")

    def update_progress(
        self,
        run_id: str,
        *,
        stage: str | None = None,
        evaluations_done: int | None = None,
        evaluation_budget: int | None = None,
        message: str | None = None,
    ) -> None:
        sets, args = [], []
        if stage is not None:
            sets.append("stage=?")
            args.append(stage)
        if evaluations_done is not None:
            sets.append("evaluations_done=?")
            args.append(int(evaluations_done))
        if evaluation_budget is not None:
            sets.append("evaluation_budget=?")
            args.append(int(evaluation_budget))
        if message is not None:
            sets.append("progress_message=?")
            args.append(message[:300])
        if not sets:
            return
        with self.tx() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE run_id=? AND status='running'", [*args, run_id])

    def record_stage(self, run_id: str, stage: str, message: str, data: dict[str, Any] | None = None) -> None:
        payload = {"stage": stage, "message": message[:300], **(data or {})}
        with self.tx() as conn:
            cur = conn.execute(
                "UPDATE jobs SET stage=?, progress_message=? WHERE run_id=? AND status='running'", (stage, message[:300], run_id)
            )
            if cur.rowcount == 1:
                self._insert_event(conn, run_id, "stage", stage, payload)

    def record_iterations(self, run_id: str, stage: str | None, points: list[dict[str, Any]]) -> int | None:
        """One coalesced `iteration` event: latest point fields + all points since
        the previous one. Also advances the progress counter."""
        if not points:
            return None
        latest = points[-1]
        data = {**latest, "points": points}
        with self.tx() as conn:
            conn.execute(
                "UPDATE jobs SET evaluations_done=MAX(evaluations_done, ?) WHERE run_id=? AND status='running'",
                (int(latest.get("evaluation") or 0), run_id),
            )
            eid = self._insert_event(conn, run_id, "iteration", stage, data)
            self._prune(conn, run_id)
            return eid

    def set_fields(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.tx() as conn:
            conn.execute(f"UPDATE jobs SET {sets} WHERE run_id=?", [*fields.values(), run_id])

    def reconcile_interrupted(self) -> list[str]:
        """After a restart: every job still marked running belonged to a dead
        worker. Mark each failed exactly once; queued jobs stay queued."""
        with self.tx() as conn:
            rows = conn.execute("SELECT run_id FROM jobs WHERE status='running'").fetchall()
            ids = [r["run_id"] for r in rows]
            for rid in ids:
                cur = conn.execute(
                    """UPDATE jobs SET status='failed', finished_at=?, error_code='interrupted_by_restart',
                           error_message=? WHERE run_id=? AND status='running'""",
                    (iso(), "The worker stopped while this job was running (server restart). It was not re-run automatically.", rid),
                )
                if cur.rowcount == 1:
                    job = conn.execute("SELECT * FROM jobs WHERE run_id=?", (rid,)).fetchone()
                    self._insert_event(conn, rid, "status", job["stage"], self.status_dict(job, conn=conn))
                    self._insert_event(
                        conn, rid, "error", job["stage"], {"code": "interrupted_by_restart", "message": job["error_message"]}
                    )
            return ids

    # ------------------------------------------------------------------ #
    # events
    # ------------------------------------------------------------------ #
    def _insert_event(self, conn: sqlite3.Connection, run_id: str, type_: str, stage: str | None, data: dict[str, Any]) -> int:
        cur = conn.execute(
            "INSERT INTO events(run_id, ts, type, stage, data_json) VALUES (?,?,?,?,?)",
            (run_id, iso(), type_, stage, dumps(data)),
        )
        return int(cur.lastrowid or 0)

    def add_event(self, run_id: str, type_: str, stage: str | None, data: dict[str, Any]) -> int:
        with self.tx() as conn:
            eid = self._insert_event(conn, run_id, type_, stage, data)
            self._prune(conn, run_id)
            return eid

    def _prune(self, conn: sqlite3.Connection, run_id: str) -> None:
        n = conn.execute("SELECT COUNT(*) FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
        excess = int(n) - self.max_events_per_run
        if excess <= 0:
            return
        placeholders = ",".join("?" for _ in _KEEP_TYPES)
        conn.execute(
            f"""DELETE FROM events WHERE id IN (
                    SELECT id FROM events WHERE run_id=? AND type NOT IN ({placeholders}) ORDER BY id LIMIT ?)""",
            (run_id, *_KEEP_TYPES, excess),
        )

    def events_after(self, run_id: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT id, run_id, ts, type, stage, data_json FROM events WHERE run_id=? AND id>? ORDER BY id LIMIT ?",
            (run_id, int(after), int(limit)),
        ).fetchall()
        return [
            {"id": r["id"], "run_id": r["run_id"], "ts": r["ts"], "type": r["type"], "stage": r["stage"], "data": json.loads(r["data_json"])}
            for r in rows
        ]

    # ------------------------------------------------------------------ #
    # worker heartbeat / meta
    # ------------------------------------------------------------------ #
    def heartbeat(self, pid: int, started_at: str, current_run: str | None, child_pid: int | None, info: dict[str, Any]) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO worker_heartbeat(id, pid, started_at, beat_at, current_run, child_pid, info_json)
                   VALUES (1,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET pid=excluded.pid, started_at=excluded.started_at, beat_at=excluded.beat_at,
                       current_run=excluded.current_run, child_pid=excluded.child_pid, info_json=excluded.info_json""",
                (pid, started_at, iso(), current_run, child_pid, dumps(info)),
            )

    def get_heartbeat(self) -> sqlite3.Row | None:
        return self._conn().execute("SELECT * FROM worker_heartbeat WHERE id=1").fetchone()

    def clear_heartbeat(self, pid: int) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM worker_heartbeat WHERE id=1 AND pid=?", (pid,))

    def set_meta(self, key: str, value: str | None) -> None:
        with self.tx() as conn:
            if value is None:
                conn.execute("DELETE FROM meta WHERE key=?", (key,))
            else:
                conn.execute("INSERT INTO meta(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_meta(self, key: str) -> str | None:
        row = self._conn().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return None if row is None else row["value"]

    # ------------------------------------------------------------------ #
    # example run / IBM records
    # ------------------------------------------------------------------ #
    def example_job(self) -> sqlite3.Row | None:
        return self._conn().execute(
            "SELECT * FROM jobs WHERE is_example=1 AND status='succeeded' ORDER BY seq DESC LIMIT 1"
        ).fetchone()

    def import_example(self, *, run_id: str, kind: str, config: dict[str, Any], config_hash: str, cache_key: str | None,
                       created_at: str, finished_at: str, label: str, summary: dict[str, Any], result_path: str,
                       evaluations_done: int, evaluation_budget: int | None) -> bool:
        with self.tx() as conn:
            if conn.execute("SELECT 1 FROM jobs WHERE run_id=?", (run_id,)).fetchone():
                conn.execute("UPDATE jobs SET is_example=1, label=?, result_path=? WHERE run_id=?", (label, result_path, run_id))
                return False
            conn.execute(
                """INSERT INTO jobs(run_id, kind, status, created_at, started_at, finished_at, config_json, config_hash, cache_key,
                       timeout_s, stage, label, is_example, summary_json, result_path, evaluations_done, evaluation_budget)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, kind, "succeeded", created_at, created_at, finished_at, dumps(config), config_hash, cache_key, 0,
                 "done", label, 1, dumps(summary), result_path, evaluations_done, evaluation_budget),
            )
            self._insert_event(conn, run_id, "stage", "done", {"stage": "done", "message": "Saved example run (recorded earlier)"})
            job = conn.execute("SELECT * FROM jobs WHERE run_id=?", (run_id,)).fetchone()
            self._insert_event(conn, run_id, "status", "done", self.status_dict(job, conn=conn))
            self._insert_event(conn, run_id, "result", "done", {"run_id": run_id})
            return True

    def record_ibm_job(self, ibm_job_id: str, run_id: str, backend: str, shots: int, status: str, detail: str) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO ibm_jobs(ibm_job_id, run_id, backend, shots, submitted_at, status, detail) VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(ibm_job_id) DO UPDATE SET status=excluded.status, detail=excluded.detail""",
                (ibm_job_id, run_id, backend, shots, iso(), status, detail),
            )

    def ibm_jobs(self, run_id: str | None = None) -> list[sqlite3.Row]:
        if run_id:
            return self._conn().execute("SELECT * FROM ibm_jobs WHERE run_id=? ORDER BY submitted_at DESC", (run_id,)).fetchall()
        return self._conn().execute("SELECT * FROM ibm_jobs ORDER BY submitted_at DESC LIMIT 100").fetchall()

    # ------------------------------------------------------------------ #
    # shaping
    # ------------------------------------------------------------------ #
    def status_dict(self, job: sqlite3.Row, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        err = None
        if job["error_code"]:
            err = {"code": job["error_code"], "message": job["error_message"] or job["error_code"]}
        return {
            "run_id": job["run_id"],
            "kind": job["kind"],
            "status": job["status"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "queue_position": self.queue_position(job, conn),
            "progress": {
                "stage": job["stage"],
                "evaluations_done": job["evaluations_done"],
                "evaluation_budget": job["evaluation_budget"],
                "message": job["progress_message"],
            },
            "error": err,
            "config_hash": job["config_hash"],
            "idempotency_key": job["idempotency_key"],
            "cached_from": job["cached_from"],
            "cached_original_time": job["cached_original_time"],
            "label": job["label"],
            "is_example": bool(job["is_example"]),
            "cancel_requested": bool(job["cancel_requested"]),
            "timeout_seconds": job["timeout_s"] or None,
        }

    def summary_dict(self, job: sqlite3.Row) -> dict[str, Any]:
        s = json.loads(job["summary_json"] or "{}")
        err = None
        if job["error_code"]:
            err = {"code": job["error_code"], "message": job["error_message"] or job["error_code"]}
        return {
            "run_id": job["run_id"],
            "kind": job["kind"],
            "status": job["status"],
            "created_at": job["created_at"],
            "finished_at": job["finished_at"],
            "label": job["label"],
            "n_symbols": int(s.get("n_symbols") or 0),
            "total_qubits": s.get("total_qubits"),
            "depth": s.get("depth"),
            "optimizer": s.get("optimizer"),
            "qaoa_status": s.get("qaoa_status"),
            "best_objective": s.get("best_objective"),
            "is_example": bool(job["is_example"]),
            "cached_from": job["cached_from"],
            "error": err,
        }
