"""Optional IBM Quantum access.

* `local_status` never touches the network and never consumes quota: it only
  reports whether a token/instance is configured in the environment.
* `check_access` is an explicit, user-initiated check that lists the
  backends visible to the account (no jobs are submitted, no quota is used).
* `submit_counts_job` is the optional hardware path. It is reachable only from
  the command line with `--confirm`, never from the web UI, tests or load
  tests, and it refuses to run without an explicit `confirm=True`.

The token is read from the environment (`IBM_QUANTUM_TOKEN`) and is never
logged, returned, written to reports or passed to the job worker.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import sys
from typing import Any

from .contracts import IbmStatus
from .logging_utils import safe_message
from .settings import Settings, get_settings

log = logging.getLogger("quantfolio.ibm")

CHANNEL = "ibm_quantum_platform"
CHECK_TIMEOUT_S = 25.0


def local_status(settings: Settings) -> IbmStatus:
    if not settings.ibm_configured:
        return IbmStatus(
            configured=False,
            instance_configured=bool(settings.ibm_instance),
            detail="Not configured. Set IBM_QUANTUM_TOKEN (and optionally IBM_QUANTUM_INSTANCE) in .env to enable the optional "
            "hardware path. All app features run on local simulators without it.",
        )
    return IbmStatus(
        configured=True,
        instance_configured=bool(settings.ibm_instance),
        detail="Token present (not verified; no network call made). Use 'Check access' to list backends without using quota. "
        "Hardware runs are command-line only and need explicit confirmation.",
    )


def _service(settings: Settings) -> Any:
    from qiskit_ibm_runtime import QiskitRuntimeService

    kwargs: dict[str, Any] = {"channel": CHANNEL, "token": settings.ibm_token()}
    if settings.ibm_instance:
        kwargs["instance"] = settings.ibm_instance
    return QiskitRuntimeService(**kwargs)


def check_access(settings: Settings) -> IbmStatus:
    """List operational backends. Uses the network, never consumes quota."""
    from .api.errors import ApiError

    if not settings.ibm_configured:
        raise ApiError(400, "ibm_not_configured", "IBM_QUANTUM_TOKEN is not set; nothing to check.")

    def work() -> list[str]:
        svc = _service(settings)
        return sorted(b.name for b in svc.backends(operational=True, simulator=False))

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        names = pool.submit(work).result(timeout=CHECK_TIMEOUT_S)
    except concurrent.futures.TimeoutError as exc:
        raise ApiError(504, "ibm_timeout", "IBM Quantum did not answer in time.") from exc
    except Exception as exc:
        log.warning("IBM access check failed: %s", safe_message(f"{type(exc).__name__}: {exc}"))
        raise ApiError(502, "ibm_check_failed", "IBM Quantum access check failed: " + safe_message(f"{type(exc).__name__}: {exc}", 200)) from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return IbmStatus(configured=True, instance_configured=bool(settings.ibm_instance), checked=True, backends=names,
                     detail=f"Access verified; {len(names)} operational backend(s) visible. No jobs were submitted.")


def submit_counts_job(settings: Settings, circuit: Any, *, backend_name: str | None, shots: int, confirm: bool) -> dict[str, Any]:
    """Run one already-bound circuit on IBM hardware and return the counts.

    This consumes the account's quota. It runs only with confirm=True and is
    wired to the command line only."""
    if not confirm:
        raise PermissionError("Hardware submission needs explicit confirmation (confirm=True / --confirm).")
    if not settings.ibm_configured:
        raise RuntimeError("IBM_QUANTUM_TOKEN is not set.")
    if not 1 <= shots <= 4096:
        raise ValueError("shots must be between 1 and 4096")
    from qiskit import transpile
    from qiskit_ibm_runtime import SamplerV2

    svc = _service(settings)
    backend = svc.backend(backend_name) if backend_name else svc.least_busy(operational=True, simulator=False)
    isa = transpile(circuit, backend=backend, optimization_level=1, seed_transpiler=7)
    job = SamplerV2(mode=backend).run([isa], shots=shots)
    res = job.result()[0]
    counts = res.data.meas.get_counts() if hasattr(res.data, "meas") else next(iter(res.data.values())).get_counts()
    return {"ibm_job_id": job.job_id(), "backend": backend.name, "shots": shots, "counts": counts,
            "transpiled_depth": isa.depth(), "two_qubit_gates": sum(v for k, v in isa.count_ops().items() if k in ("cx", "cz", "ecr"))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m quantfolio.ibm", description="Optional IBM Quantum tools (never used by tests).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="local configuration status (no network)")
    sub.add_parser("check", help="list backends visible to the account (network, no quota)")
    sp = sub.add_parser("submit", help="re-sample a stored run's optimised QAOA circuit on hardware (uses quota)")
    sp.add_argument("--run-id", required=True)
    sp.add_argument("--backend", default=None)
    sp.add_argument("--shots", type=int, default=1024)
    sp.add_argument("--confirm", action="store_true", help="required: acknowledges that this consumes IBM quota")
    args = ap.parse_args(argv)
    s = get_settings()
    if args.cmd == "status":
        print(local_status(s).model_dump_json(indent=2))
        return 0
    if args.cmd == "check":
        try:
            print(check_access(s).model_dump_json(indent=2))
            return 0
        except Exception as exc:  # ApiError
            print(safe_message(getattr(exc, "message", str(exc))), file=sys.stderr)
            return 1
    # submit
    if not args.confirm:
        print("Refusing: add --confirm to acknowledge that this consumes IBM Quantum quota.", file=sys.stderr)
        return 2
    try:
        from .quantum import bound_circuit_for_run  # provided by the quantum core, if available
    except ImportError:
        print("The quantum core does not expose bound_circuit_for_run(); hardware re-sampling is unavailable.", file=sys.stderr)
        return 3
    from .jobs.fsutil import is_valid_run_id

    if not is_valid_run_id(args.run_id):
        print("invalid run id", file=sys.stderr)
        return 2
    result_path = s.runs_dir / args.run_id / "result.json"
    circuit = bound_circuit_for_run(json.loads(result_path.read_text(encoding="utf-8")))
    out = submit_counts_job(s, circuit, backend_name=args.backend, shots=args.shots, confirm=True)
    from .jobs.store import JobStore

    JobStore(s.db_path).record_ibm_job(out["ibm_job_id"], args.run_id, out["backend"], out["shots"], "completed", "cli re-sample")
    dest = s.runs_dir / args.run_id / f"ibm_{out['ibm_job_id']}.json"
    from .jobs.fsutil import atomic_write_text, dumps

    atomic_write_text(dest, dumps(out))
    print(f"Saved hardware counts for {args.run_id} ({out['backend']}, {out['shots']} shots).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
