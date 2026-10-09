"""Firestore-backed job store for the cloud deployment.

Documents
---------
runs/{run_id}                    run metadata + submitting client key (mirrors the SQLite `jobs` columns)
runs/{run_id}/events/{seq}       compact progress events (seq is per run, increasing)
idempotency/{sha256(uid, key)}   Idempotency-Key -> run (scoped per client)
cache/{sha256(kind, key)}        latest successful result for a configuration (shared workspace)
days/{YYYY-MM-DD}                quota counters for one UTC day
users/{uid}/datasets/{id}        which client uploaded a dataset
control/scheduler                bounded queue + the single global lease + dispatch outbox state
control/compute                  kill switch

`uid` is never a person: it is the salted hash of the client's network address
(`client.py`). Runs are public in one shared workspace; per-client keys only
drive quotas, idempotency, cancellation rights and upload visibility.

Guarantees
----------
* A run is persisted, queued and counted against the quotas in ONE transaction;
  the worker is only launched after that commit (`decide_kick`), and any later
  request or the scheduled sweep re-launches it if that launch was lost.
* At most one worker holds the lease. The lease carries a fencing token that is
  stamped on the run when it is claimed; every worker write is conditional on
  "run is still running under my token", so a stalled or duplicate worker can
  never publish over a run that was reconciled, cancelled or finished.
* A worker that stops heart-beating loses the lease after `lease_ttl_s`; its
  run is then marked failed (`worker_lost`). Runs are never re-executed
  automatically, so a retry cannot publish a conflicting result.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from ..api.errors import ApiError
from .config import CloudConfig
from .db import DocStore, Txn

SCHED = "control/scheduler"
COMPUTE = "control/compute"
TERMINAL_STATES = ("succeeded", "failed", "cancelled", "timed_out")
ACTIVE_STATES = ("queued", "running")
KEEP_TYPES = ("status", "stage", "result", "error")
_MAX_EVENT_CHARS = 200_000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def new_run_id(kind: str, now: datetime) -> str:
    return f"{'exp' if kind == 'experiment' else 'run'}-{now:%Y%m%d}-{secrets.token_hex(4)}"


def run_path(run_id: str) -> str:
    return f"runs/{run_id}"


def _sha(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), allow_nan=False, default=str)


def _empty_sched() -> dict[str, Any]:
    return {"queue": [], "running": None, "lease": None, "fence": 0, "dispatch": {}}


class CloudJobStore:
    def __init__(self, db: DocStore, cfg: CloudConfig, *, max_events_per_run: int = 3000,
                 clock: Callable[[], datetime] = utcnow) -> None:
        self.db = db
        self.cfg = cfg
        self.max_events_per_run = max_events_per_run
        self.clock = clock

    # ------------------------------------------------------------------ helpers
    def _expire_at(self, now: datetime) -> datetime:
        return now + timedelta(days=self.cfg.retention_days)

    def day_key(self, now: datetime | None = None) -> str:
        return f"{(now or self.clock()):%Y-%m-%d}"

    def resets_at(self, now: datetime | None = None) -> str:
        n = now or self.clock()
        return iso(datetime(n.year, n.month, n.day, tzinfo=timezone.utc) + timedelta(days=1))

    def _event(self, t: Txn, run: dict[str, Any], type_: str, stage: str | None, data: dict[str, Any], now: datetime) -> int | None:
        """Append one event (a write: call only after every read of the transaction). Mutates run['event_seq']."""
        if type_ not in KEEP_TYPES:
            if int(run.get("event_extra") or 0) >= self.max_events_per_run:
                return None  # bounded history: late iteration/log events are dropped, outcomes never are
            run["event_extra"] = int(run.get("event_extra") or 0) + 1
        payload = _dumps(data)
        if len(payload) > _MAX_EVENT_CHARS and isinstance(data.get("points"), list):
            payload = _dumps({**data, "points": data["points"][-50:], "truncated": True})
        seq = int(run.get("event_seq") or 0) + 1
        run["event_seq"] = seq
        t.set(f"runs/{run['run_id']}/events/{seq:012d}",
              {"seq": seq, "ts": iso(now), "type": type_, "stage": stage, "data_json": payload, "expire_at": run.get("expire_at")})
        return seq

    @staticmethod
    def _entries(sched: dict[str, Any]) -> list[dict[str, Any]]:
        running = sched.get("running")
        return ([running] if running else []) + list(sched.get("queue") or [])

    @staticmethod
    def queue_position(run: dict[str, Any], sched: dict[str, Any] | None) -> int | None:
        if run.get("status") != "queued" or not sched:
            return None
        for i, entry in enumerate(sched.get("queue") or []):
            if entry.get("run_id") == run["run_id"]:
                return i + (1 if sched.get("running") else 0) + 1
        return None

    def status_dict(self, run: dict[str, Any], sched: dict[str, Any] | None = None) -> dict[str, Any]:
        if run.get("status") == "queued" and sched is None:
            sched = self.db.get(SCHED)
        err = None
        if run.get("error_code"):
            err = {"code": run["error_code"], "message": run.get("error_message") or run["error_code"]}
        return {
            "run_id": run["run_id"],
            "kind": run["kind"],
            "status": run["status"],
            "created_at": run["created_at"],
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "queue_position": self.queue_position(run, sched),
            "progress": {
                "stage": run.get("stage") or "queued",
                "evaluations_done": int(run.get("evaluations_done") or 0),
                "evaluation_budget": run.get("evaluation_budget"),
                "message": run.get("progress_message"),
            },
            "error": err,
            "config_hash": run["config_hash"],
            "idempotency_key": run.get("idempotency_key"),
            "cached_from": run.get("cached_from"),
            "cached_original_time": run.get("cached_original_time"),
            "label": run.get("label"),
            "is_example": False,
            "cancel_requested": bool(run.get("cancel_requested")),
            "timeout_seconds": run.get("timeout_s") or None,
        }

    @staticmethod
    def summary_dict(run: dict[str, Any]) -> dict[str, Any]:
        s = json.loads(run.get("summary_json") or "{}")
        err = None
        if run.get("error_code"):
            err = {"code": run["error_code"], "message": run.get("error_message") or run["error_code"]}
        return {
            "run_id": run["run_id"],
            "kind": run["kind"],
            "status": run["status"],
            "created_at": run["created_at"],
            "finished_at": run.get("finished_at"),
            "label": run.get("label"),
            "n_symbols": int(s.get("n_symbols") or 0),
            "total_qubits": s.get("total_qubits"),
            "depth": s.get("depth"),
            "optimizer": s.get("optimizer"),
            "qaoa_status": s.get("qaoa_status"),
            "best_objective": s.get("best_objective"),
            "is_example": False,
            "cached_from": run.get("cached_from"),
            "error": err,
        }

    # ------------------------------------------------------------------ kill switch
    def _compute_from(self, doc: dict[str, Any] | None) -> tuple[bool, str | None]:
        if not self.cfg.compute_enabled:
            return False, "Compute is switched off in the service configuration."
        if doc is not None and doc.get("enabled") is False:
            return False, doc.get("reason") or None
        return True, None

    def compute_state(self) -> tuple[bool, str | None]:
        return self._compute_from(self.db.get(COMPUTE))

    def set_compute(self, enabled: bool, reason: str | None, by: str) -> dict[str, Any]:
        doc = {"enabled": bool(enabled), "reason": (reason or None), "updated_at": iso(self.clock()), "updated_by": by}
        self.db.set(COMPUTE, doc)
        return doc

    # ------------------------------------------------------------------ reads
    def get(self, run_id: str) -> dict[str, Any] | None:
        return self.db.get(run_path(run_id))

    def get_owned(self, run_id: str, uid: str) -> dict[str, Any] | None:
        run = self.get(run_id)
        return run if run is not None and run.get("uid") == uid else None

    def events_after(self, run_id: str, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.db.query(f"runs/{run_id}/events", where=[("seq", ">", int(after))], order_by="seq", limit=int(limit))
        return [{"id": r["seq"], "run_id": run_id, "ts": r["ts"], "type": r["type"], "stage": r.get("stage"),
                 "data": json.loads(r.get("data_json") or "{}")} for r in rows]

    def lookup_idempotency(self, uid: str, key: str) -> tuple[str, str] | None:
        doc = self.db.get(f"idempotency/{_sha(uid, key)}")
        return None if doc is None else (doc["run_id"], doc["request_hash"])

    def find_cached(self, kind: str, cache_key: str) -> dict[str, Any] | None:
        return self.db.get(f"cache/{_sha(kind, cache_key)}")

    def list_recent(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Newest runs of the shared workspace (single-field index on created_at; no composite index)."""
        return self.db.query("runs", order_by="created_at", descending=True, limit=limit or self.cfg.history_cap)

    def snapshot(self) -> dict[str, Any]:
        """The three shared documents behind `overview` (callers may cache this for a few seconds)."""
        now = self.clock()
        return {"now": now, "sched": self.db.get(SCHED) or _empty_sched(), "day": self.db.get(f"days/{self.day_key(now)}") or {},
                "compute": self.db.get(COMPUTE)}

    def overview(self, uid: str | None, snap: dict[str, Any] | None = None) -> dict[str, Any]:
        """Quota, queue and kill-switch view for one caller."""
        snap = snap or self.snapshot()
        now, sched, day = snap["now"], snap["sched"], snap["day"]
        enabled, reason = self._compute_from(snap["compute"])
        entries = self._entries(sched)
        total = int(day.get("total") or 0)
        used = int((day.get("users") or {}).get(uid, 0)) if uid else 0
        active_cap = self.cfg.user_active_cap
        return {
            "compute_enabled": enabled,
            "compute_reason": reason,
            "quota": {
                "day": self.day_key(now),
                "resets_at": self.resets_at(now),
                "global_limit": self.cfg.daily_run_cap,
                "global_used": total,
                "global_remaining": max(0, self.cfg.daily_run_cap - total),
                "user_limit": self.cfg.user_daily_run_cap,
                "user_used": used,
                "user_remaining": max(0, self.cfg.user_daily_run_cap - used),
                "user_active_limit": active_cap,
                "user_active": sum(1 for e in entries if uid and e.get("uid") == uid),
            },
            "queue": {"depth": len(entries), "capacity": self.cfg.queue_capacity, "running": bool(sched.get("running"))},
        }

    # ------------------------------------------------------------------ submission
    def submit(self, *, uid: str, operator: bool, kind: str, config: dict[str, Any], config_hash: str, cache_key: str | None,
               timeout_s: int, idempotency_key: str | None, request_hash: str, evaluation_budget: int | None,
               summary: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
        """Persist + enqueue + count a new run in one transaction. Returns (run, created)."""
        cfg = self.cfg
        now = self.clock()
        new_id = new_run_id(kind, now)
        day_path = f"days/{self.day_key(now)}"
        idem_path = f"idempotency/{_sha(uid, idempotency_key)}" if idempotency_key else None

        def _txn(t: Txn) -> tuple[dict[str, Any], bool]:
            sched = t.get(SCHED) or _empty_sched()
            compute = t.get(COMPUTE)
            day = t.get(day_path) or {}
            idem = t.get(idem_path) if idem_path else None
            if idem is not None:
                if idem.get("request_hash") != request_hash:
                    raise ApiError(409, "idempotency_key_reused", "This Idempotency-Key was already used with a different request body.")
                existing = t.get(run_path(idem["run_id"]))
                if existing is not None:
                    return existing, False
            entries = self._entries(sched)
            twin = next((e for e in entries if e.get("uid") == uid and e.get("kind") == kind and e.get("config_hash") == config_hash), None)
            twin_run = t.get(run_path(twin["run_id"])) if twin else None
            # ---- all reads done; checks and writes below
            if twin_run is not None and twin_run.get("status") in ACTIVE_STATES:
                if idem_path and idem is None:
                    t.set(idem_path, {"uid": uid, "run_id": twin_run["run_id"], "request_hash": request_hash,
                                      "created_at": iso(now), "expire_at": self._expire_at(now)})
                return twin_run, False
            enabled, reason = self._compute_from(compute)
            if not enabled:
                raise ApiError(503, "compute_paused", "New runs are paused right now. Stored results stay available.",
                               {"reason": reason}, headers={"Retry-After": "300"})
            mine = [e["run_id"] for e in entries if e.get("uid") == uid]
            active_cap = cfg.user_active_cap
            if len(mine) >= active_cap:
                raise ApiError(429, "user_active_limit", "A run from your network is already in progress. Wait for it to finish or cancel it, then submit again.",
                               {"limit": active_cap, "active": mine})
            users = dict(day.get("users") or {})
            used = int(users.get(uid) or 0)
            if not operator and used >= cfg.user_daily_run_cap:
                raise ApiError(429, "user_quota_exhausted",
                               f"Your network has used its {cfg.user_daily_run_cap} live runs for today (the per-visitor "
                               "limit of this public demo). It resets at 00:00 UTC; finished runs stay readable.",
                               {"scope": "user", "limit": cfg.user_daily_run_cap, "used": used, "resets_at": self.resets_at(now)})
            total = int(day.get("total") or 0)
            if total >= cfg.daily_run_cap:
                raise ApiError(429, "daily_quota_exhausted", f"Today's shared limit of {cfg.daily_run_cap} live runs has been reached.",
                               {"scope": "global", "limit": cfg.daily_run_cap, "used": total, "resets_at": self.resets_at(now)})
            depth = len(entries)
            if depth >= cfg.queue_capacity:
                retry = max(5, min(120, 10 * (depth - cfg.queue_capacity + 1)))
                raise ApiError(429, "queue_full", f"The job queue is full ({depth}/{cfg.queue_capacity}). Try again shortly.",
                               {"queue_depth": depth, "capacity": cfg.queue_capacity}, headers={"Retry-After": str(retry)})
            run: dict[str, Any] = {
                "run_id": new_id, "uid": uid, "kind": kind, "status": "queued", "created_at": iso(now), "started_at": None,
                "finished_at": None, "config_json": _dumps(config), "config_hash": config_hash, "cache_key": cache_key,
                "idempotency_key": idempotency_key, "cancel_requested": False, "timeout_s": int(timeout_s), "stage": "queued",
                "evaluations_done": 0, "evaluation_budget": evaluation_budget, "progress_message": None, "error_code": None,
                "error_message": None, "cached_from": None, "cached_original_time": None, "label": None, "attempts": 0,
                "summary_json": _dumps(summary or {}), "result_path": None, "counts_path": None, "fence": None,
                "execution": None, "day": self.day_key(now), "counted": True, "event_seq": 0, "event_extra": 0,
                "expire_at": self._expire_at(now),
            }
            sched["queue"] = [*(sched.get("queue") or []), {"run_id": new_id, "uid": uid, "kind": kind, "config_hash": config_hash,
                                                             "timeout_s": int(timeout_s), "enqueued_at": iso(now)}]
            users[uid] = used + 1
            day.update({"total": total + 1, "users": users, "expire_at": self._expire_at(now)})
            self._event(t, run, "stage", "queued", {"stage": "queued", "message": "Accepted and queued"}, now)
            self._event(t, run, "status", "queued", self.status_dict(run, sched), now)
            t.set(run_path(new_id), run)
            t.set(SCHED, sched)
            t.set(day_path, day)
            if idem_path:
                t.set(idem_path, {"uid": uid, "run_id": new_id, "request_hash": request_hash, "created_at": iso(now),
                                  "expire_at": self._expire_at(now)})
            return run, True

        return self.db.transaction(_txn)

    def create_replay(self, *, uid: str, operator: bool, source: dict[str, Any], config: dict[str, Any], config_hash: str,
                      cache_key: str, idempotency_key: str | None, request_hash: str) -> dict[str, Any]:
        """A new history entry that points at a stored result. No compute, no live-run quota."""
        cfg = self.cfg
        now = self.clock()
        new_id = new_run_id(source["kind"], now)
        day_path = f"days/{self.day_key(now)}"
        idem_path = f"idempotency/{_sha(uid, idempotency_key)}" if idempotency_key else None

        def _txn(t: Txn) -> dict[str, Any]:
            day = t.get(day_path) or {}
            idem = t.get(idem_path) if idem_path else None
            if idem is not None:
                if idem.get("request_hash") != request_hash:
                    raise ApiError(409, "idempotency_key_reused", "This Idempotency-Key was already used with a different request body.")
                existing = t.get(run_path(idem["run_id"]))
                if existing is not None:
                    return existing
            replays = dict(day.get("user_replays") or {})
            used, total = int(replays.get(uid) or 0), int(day.get("replays") or 0)
            if not operator and (used >= cfg.user_daily_replay_cap or total >= cfg.daily_replay_cap):
                scope = "user" if used >= cfg.user_daily_replay_cap else "global"
                raise ApiError(429, "user_quota_exhausted" if scope == "user" else "daily_quota_exhausted",
                               "Too many stored-result replays from your network today. Open the earlier run from History instead.",
                               {"scope": scope, "kind": "replay", "limit": cfg.user_daily_replay_cap if scope == "user" else cfg.daily_replay_cap,
                                "used": used if scope == "user" else total, "resets_at": self.resets_at(now)})
            run: dict[str, Any] = {
                "run_id": new_id, "uid": uid, "kind": source["kind"], "status": "succeeded", "created_at": iso(now),
                "started_at": iso(now), "finished_at": iso(now), "config_json": _dumps(config), "config_hash": config_hash,
                "cache_key": cache_key, "idempotency_key": idempotency_key, "cancel_requested": False,
                "timeout_s": int(source.get("timeout_s") or 0), "stage": "done",
                "evaluations_done": int(source.get("evaluations_done") or 0), "evaluation_budget": source.get("evaluation_budget"),
                "progress_message": None, "error_code": None, "error_message": None, "cached_from": source["run_id"],
                "cached_original_time": source.get("finished_at"), "label": f"Cached replay of {source['run_id']}", "attempts": 0,
                "summary_json": source.get("summary_json") or "{}", "result_path": source["result_path"],
                "counts_path": source.get("counts_path"), "fence": None, "execution": None, "day": self.day_key(now),
                "counted": False, "event_seq": 0, "event_extra": 0, "expire_at": self._expire_at(now),
            }
            replays[uid] = used + 1
            day.update({"replays": total + 1, "user_replays": replays, "expire_at": self._expire_at(now)})
            self._event(t, run, "stage", "done", {"stage": "done", "message": f"Replayed stored result of {source['run_id']} (no new computation)"}, now)
            self._event(t, run, "status", "done", self.status_dict(run, {}), now)
            self._event(t, run, "result", "done", {"run_id": new_id, "cached_from": source["run_id"]}, now)
            t.set(run_path(new_id), run)
            t.set(day_path, day)
            if idem_path and idem is None:
                t.set(idem_path, {"uid": uid, "run_id": new_id, "request_hash": request_hash, "created_at": iso(now),
                                  "expire_at": self._expire_at(now)})
            return run

        return self.db.transaction(_txn)

    def request_cancel(self, run_id: str, uid: str) -> tuple[str, dict[str, Any] | None]:
        """('cancelled' | 'requested' | 'terminal' | 'missing', run). Durable: stored on the run document."""
        now = self.clock()

        def _txn(t: Txn) -> tuple[str, dict[str, Any] | None]:
            run = t.get(run_path(run_id))
            if run is None or run.get("uid") != uid:
                return "missing", None
            if run["status"] == "queued":
                sched = t.get(SCHED) or _empty_sched()
                day_path = f"days/{run.get('day')}"
                day = t.get(day_path) if run.get("counted") else None
                run.update({"status": "cancelled", "finished_at": iso(now), "cancel_requested": True,
                            "progress_message": "Cancelled before start", "counted": False})
                sched["queue"] = [e for e in (sched.get("queue") or []) if e.get("run_id") != run_id]
                if day is not None:  # nothing was computed: give the quota back
                    users = dict(day.get("users") or {})
                    users[uid] = max(0, int(users.get(uid) or 0) - 1)
                    day.update({"total": max(0, int(day.get("total") or 0) - 1), "users": users})
                    t.set(day_path, day)
                self._event(t, run, "status", run.get("stage"), self.status_dict(run, sched), now)
                t.set(run_path(run_id), run)
                t.set(SCHED, sched)
                return "cancelled", run
            if run["status"] == "running":
                if not run.get("cancel_requested"):
                    run["cancel_requested"] = True
                    self._event(t, run, "log", run.get("stage"), {"level": "info", "message": "Cancellation requested"}, now)
                    t.set(run_path(run_id), run)
                return "requested", run
            return "terminal", run

        return self.db.transaction(_txn)

    # ------------------------------------------------------------------ dispatch outbox
    def decide_kick(self, *, force: bool = False) -> bool:
        """True iff the caller should now launch one worker execution (and nobody else will for a while)."""
        now = self.clock()

        def _txn(t: Txn) -> bool:
            sched = t.get(SCHED)
            compute = t.get(COMPUTE)
            if not sched or not sched.get("queue"):
                return False
            lease = sched.get("lease")
            if lease and float(lease.get("expires_at") or 0) > now.timestamp():
                return False  # a live worker will drain the queue itself
            if not self._compute_from(compute)[0]:
                return False
            dispatch = dict(sched.get("dispatch") or {})
            if not force and now.timestamp() - float(dispatch.get("last_kick_ts") or 0) < self.cfg.kick_min_interval_s:
                return False
            dispatch.update({"last_kick_ts": now.timestamp(), "last_kick_at": iso(now), "kicks": int(dispatch.get("kicks") or 0) + 1})
            sched["dispatch"] = dispatch
            t.set(SCHED, sched)
            return True

        return bool(self.db.transaction(_txn))

    def record_kick(self, *, execution: str | None = None, error: str | None = None) -> None:
        now = self.clock()

        def _txn(t: Txn) -> None:
            sched = t.get(SCHED)
            if not sched:
                return
            dispatch = dict(sched.get("dispatch") or {})
            if error:
                # let the next request retry soon instead of waiting the full interval
                dispatch.update({"last_kick_error": error[:300], "last_kick_error_at": iso(now),
                                 "last_kick_ts": now.timestamp() - self.cfg.kick_min_interval_s + 15.0})
            else:
                dispatch.update({"last_execution": execution, "last_kick_error": None})
            sched["dispatch"] = dispatch
            t.set(SCHED, sched)

        self.db.transaction(_txn)

    # ------------------------------------------------------------------ lease, claim, reconcile
    def _orphan(self, t: Txn, sched: dict[str, Any], run: dict[str, Any] | None, now: datetime) -> str | None:
        """Writes for a dead worker: fail its run (if still running) and free the slot + lease."""
        orphan = None
        if run is not None and run.get("status") == "running":
            run.update({"status": "failed", "finished_at": iso(now), "error_code": "worker_lost",
                        "error_message": "The worker stopped responding; this run was not completed. Submit it again."})
            self._event(t, run, "status", run.get("stage"), self.status_dict(run, sched), now)
            self._event(t, run, "error", run.get("stage"), {"code": "worker_lost", "message": run["error_message"]}, now)
            t.set(run_path(run["run_id"]), run)
            orphan = run["run_id"]
        sched["running"] = None
        sched["lease"] = None
        return orphan

    def reconcile(self) -> list[str]:
        """Detect a stalled/orphaned worker (expired lease). Returns the run ids that were failed."""
        now = self.clock()

        def _txn(t: Txn) -> list[str]:
            sched = t.get(SCHED)
            if not sched:
                return []
            lease, running = sched.get("lease"), sched.get("running")
            if lease and float(lease.get("expires_at") or 0) > now.timestamp():
                return []
            if not lease and not running:
                return []
            run = t.get(run_path(running["run_id"])) if running else None
            orphan = self._orphan(t, sched, run, now)
            t.set(SCHED, sched)
            return [orphan] if orphan else []

        return self.db.transaction(_txn)

    def acquire_lease(self, holder: str) -> int | None:
        """Take the global lease if it is free or expired. Returns the new fencing token, else None."""
        now = self.clock()

        def _txn(t: Txn) -> int | None:
            sched = t.get(SCHED) or _empty_sched()
            lease, running = sched.get("lease"), sched.get("running")
            if lease and float(lease.get("expires_at") or 0) > now.timestamp():
                return None
            run = t.get(run_path(running["run_id"])) if running else None
            if lease or running:
                self._orphan(t, sched, run, now)
            fence = int(sched.get("fence") or 0) + 1
            sched["fence"] = fence
            # The launched worker has arrived: the next submission after it leaves may launch immediately.
            sched["dispatch"] = {**(sched.get("dispatch") or {}), "last_kick_ts": 0.0}
            sched["lease"] = {"holder": holder, "fence": fence, "acquired_at": iso(now), "heartbeat_at": iso(now),
                              "expires_at": now.timestamp() + self.cfg.lease_ttl_s}
            t.set(SCHED, sched)
            return fence

        return self.db.transaction(_txn)

    @staticmethod
    def _holds(sched: dict[str, Any] | None, holder: str, fence: int) -> bool:
        lease = (sched or {}).get("lease")
        return bool(lease and lease.get("holder") == holder and int(lease.get("fence") or -1) == fence)

    def renew_lease(self, holder: str, fence: int) -> bool:
        now = self.clock()

        def _txn(t: Txn) -> bool:
            sched = t.get(SCHED)
            if not self._holds(sched, holder, fence):
                return False
            assert sched is not None
            sched["lease"].update({"heartbeat_at": iso(now), "expires_at": now.timestamp() + self.cfg.lease_ttl_s})
            t.set(SCHED, sched)
            return True

        return bool(self.db.transaction(_txn))

    def release_lease(self, holder: str, fence: int) -> bool:
        def _txn(t: Txn) -> bool:
            sched = t.get(SCHED)
            if not self._holds(sched, holder, fence):
                return False
            assert sched is not None
            sched["lease"] = None
            t.set(SCHED, sched)
            return True

        return bool(self.db.transaction(_txn))

    def claim_next(self, holder: str, fence: int, *, remaining_s: float, fresh: bool) -> tuple[str, dict[str, Any] | None]:
        """Atomically claim the oldest queued run, or release the lease.

        Returns ('claimed', run) or (reason, None) with reason in
        'lost' (lease taken over), 'paused' (kill switch), 'empty', 'handoff'
        (the next run does not fit in this execution's remaining time). In every
        non-claimed case except 'lost' the lease is released in the same
        transaction, so a submission either lands before (and is seen here) or
        after (and its own kick finds the lease free).
        """
        now = self.clock()

        def _txn(t: Txn) -> tuple[str, dict[str, Any] | None]:
            sched = t.get(SCHED)
            if not self._holds(sched, holder, fence):
                return "lost", None
            assert sched is not None
            compute = t.get(COMPUTE)
            queue = list(sched.get("queue") or [])
            docs = {e["run_id"]: t.get(run_path(e["run_id"])) for e in queue}
            # ---- reads done
            live = [e for e in queue if (docs[e["run_id"]] or {}).get("status") == "queued"]
            sched["queue"] = live
            reason = None
            if not self._compute_from(compute)[0]:
                reason = "paused"
            elif not live:
                reason = "empty"
            elif not fresh and float(live[0].get("timeout_s") or 0) + self.cfg.claim_margin_s > remaining_s:
                reason = "handoff"
            if reason:
                sched["lease"] = None
                t.set(SCHED, sched)
                return reason, None
            head = live[0]
            run = docs[head["run_id"]]
            assert run is not None
            run.update({"status": "running", "started_at": iso(now), "attempts": int(run.get("attempts") or 0) + 1,
                        "stage": "loading_data", "progress_message": None, "fence": fence, "execution": holder})
            sched["queue"] = live[1:]
            sched["running"] = {**head, "started_at": iso(now)}
            sched["lease"].update({"heartbeat_at": iso(now), "expires_at": now.timestamp() + self.cfg.lease_ttl_s})
            self._event(t, run, "status", "loading_data", self.status_dict(run, sched), now)
            t.set(run_path(run["run_id"]), run)
            t.set(SCHED, sched)
            return "claimed", run

        return self.db.transaction(_txn)

    # ------------------------------------------------------------------ fenced worker writes
    def _fenced(self, run: dict[str, Any] | None, fence: int) -> bool:
        return bool(run is not None and run.get("status") == "running" and run.get("fence") == fence)

    def is_cancel_requested(self, run_id: str, fence: int) -> bool:
        run = self.get(run_id)
        return bool(not self._fenced(run, fence) or (run or {}).get("cancel_requested"))

    def record_stage(self, run_id: str, fence: int, stage: str, message: str, data: dict[str, Any] | None = None) -> bool:
        now = self.clock()

        def _txn(t: Txn) -> bool:
            run = t.get(run_path(run_id))
            if not self._fenced(run, fence):
                return False
            assert run is not None
            run.update({"stage": stage, "progress_message": message[:300]})
            self._event(t, run, "stage", stage, {"stage": stage, "message": message[:300], **(data or {})}, now)
            t.set(run_path(run_id), run)
            return True

        return bool(self.db.transaction(_txn))

    def record_iterations(self, run_id: str, fence: int, stage: str | None, points: list[dict[str, Any]]) -> bool:
        if not points:
            return False
        now = self.clock()
        latest = points[-1]

        def _txn(t: Txn) -> bool:
            run = t.get(run_path(run_id))
            if not self._fenced(run, fence):
                return False
            assert run is not None
            run["evaluations_done"] = max(int(run.get("evaluations_done") or 0), int(latest.get("evaluation") or 0))
            self._event(t, run, "iteration", stage, {**latest, "points": points}, now)
            t.set(run_path(run_id), run)
            return True

        return bool(self.db.transaction(_txn))

    def add_event(self, run_id: str, fence: int, type_: str, stage: str | None, data: dict[str, Any]) -> bool:
        now = self.clock()

        def _txn(t: Txn) -> bool:
            run = t.get(run_path(run_id))
            if not self._fenced(run, fence):
                return False
            assert run is not None
            self._event(t, run, type_, stage, data, now)
            t.set(run_path(run_id), run)
            return True

        return bool(self.db.transaction(_txn))

    def finish(self, run_id: str, fence: int, to_state: str, **fields: Any) -> bool:
        """Terminal transition by the worker that owns the run. False if this worker no longer owns it."""
        if to_state not in TERMINAL_STATES:
            raise ValueError(f"not a terminal state: {to_state}")
        now = self.clock()

        def _txn(t: Txn) -> bool:
            run = t.get(run_path(run_id))
            sched = t.get(SCHED) or _empty_sched()
            if not self._fenced(run, fence):
                return False
            assert run is not None
            run.update(fields)
            run.update({"status": to_state, "finished_at": iso(now)})
            if (sched.get("running") or {}).get("run_id") == run_id:
                sched["running"] = None
                t.set(SCHED, sched)
            stage = run.get("stage")
            self._event(t, run, "status", stage, self.status_dict(run, sched), now)
            if to_state == "succeeded":
                self._event(t, run, "result", "done", {"run_id": run_id}, now)
                if run.get("cache_key") and run.get("result_path"):
                    t.set(f"cache/{_sha(run['kind'], run['cache_key'])}", {
                        "run_id": run_id, "uid": run["uid"], "kind": run["kind"], "finished_at": run["finished_at"],
                        "timeout_s": run.get("timeout_s"), "summary_json": run.get("summary_json"),
                        "result_path": run["result_path"], "counts_path": run.get("counts_path"),
                        "evaluations_done": run.get("evaluations_done"), "evaluation_budget": run.get("evaluation_budget"),
                        "expire_at": run.get("expire_at"),
                    })
            elif to_state in ("failed", "timed_out"):
                self._event(t, run, "error", stage, {"code": run.get("error_code") or to_state,
                                                     "message": run.get("error_message") or to_state}, now)
            t.set(run_path(run_id), run)
            return True

        return bool(self.db.transaction(_txn))

    # ------------------------------------------------------------------ uploaded datasets
    def dataset_owner_path(self, uid: str, dataset_id: str) -> str:
        return f"users/{uid}/datasets/{dataset_id}"

    def owned_dataset(self, uid: str, dataset_id: str) -> dict[str, Any] | None:
        return self.db.get(self.dataset_owner_path(uid, dataset_id))

    def owned_datasets(self, uid: str) -> list[dict[str, Any]]:
        return self.db.query(f"users/{uid}/datasets", limit=50)

    def check_upload_quota(self, uid: str, operator: bool) -> None:
        now = self.clock()
        day = self.db.get(f"days/{self.day_key(now)}") or {}
        self._upload_quota(day, uid, operator, now)

    def _upload_quota(self, day: dict[str, Any], uid: str, operator: bool, now: datetime) -> None:
        cfg = self.cfg
        used, total = int((day.get("user_uploads") or {}).get(uid) or 0), int(day.get("uploads") or 0)
        if operator:
            return
        if used >= cfg.user_daily_upload_cap:
            raise ApiError(429, "user_quota_exhausted", f"Your network has uploaded its {cfg.user_daily_upload_cap} datasets for today. The limit resets at 00:00 UTC.",
                           {"scope": "user", "kind": "upload", "limit": cfg.user_daily_upload_cap, "used": used,
                            "resets_at": self.resets_at(now)})
        if total >= cfg.daily_upload_cap:
            raise ApiError(429, "daily_quota_exhausted", "Today's shared upload limit has been reached.",
                           {"scope": "global", "kind": "upload", "limit": cfg.daily_upload_cap, "used": total,
                            "resets_at": self.resets_at(now)})

    def register_dataset(self, uid: str, operator: bool, info: dict[str, Any], objects: list[str]) -> bool:
        """Record that `uid` owns an uploaded dataset. Returns False if it already did (no quota used)."""
        now = self.clock()
        path = self.dataset_owner_path(uid, info["dataset_id"])
        day_path = f"days/{self.day_key(now)}"

        def _txn(t: Txn) -> bool:
            existing = t.get(path)
            day = t.get(day_path) or {}
            if existing is not None:
                return False
            self._upload_quota(day, uid, operator, now)
            uploads = dict(day.get("user_uploads") or {})
            uploads[uid] = int(uploads.get(uid) or 0) + 1
            day.update({"uploads": int(day.get("uploads") or 0) + 1, "user_uploads": uploads, "expire_at": self._expire_at(now)})
            t.set(path, {"dataset_id": info["dataset_id"], "uid": uid, "info_json": _dumps(info), "objects": objects,
                         "created_at": iso(now), "expire_at": self._expire_at(now)})
            t.set(day_path, day)
            return True

        return bool(self.db.transaction(_txn))
