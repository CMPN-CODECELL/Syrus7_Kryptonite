"""Mocked provider outage (in-process; NO network, the IBM client is replaced).

The optional IBM check is the only API path that can reach an external
provider. Here the provider "fails" (slow error) and "hangs" (timeout) while
other clients keep using the API; the API must answer 502/504 with a redacted
message and stay responsive. Prints one JSON line.
"""

from __future__ import annotations

import concurrent.futures as cf
import dataclasses
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
TOKEN = "load-test-not-a-real-token-0123456789"


def main() -> int:
    tmp_root = BACKEND.parent / ".tmp"
    tmp_root.mkdir(exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="outage-", dir=tmp_root))
    os.environ.update({"QF_DATA_DIR": str(d), "QF_PIPELINE_IMPL": "fake", "IBM_QUANTUM_TOKEN": TOKEN, "QF_LOG_LEVEL": "ERROR"})
    from fastapi.testclient import TestClient

    from quantfolio import ibm
    from quantfolio.api.app import create_app
    from quantfolio.settings import load_settings

    def failing(_s):
        time.sleep(0.3)
        raise ConnectionError(f"provider unreachable (token={TOKEN})")

    def hanging(_s):
        time.sleep(5)

    def pct(xs, q):
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))], 1)

    out: dict = {}
    settings = dataclasses.replace(load_settings(), start_worker=False)
    with TestClient(create_app(settings)) as c:
        def health(_):
            t = time.perf_counter()
            ok = c.get("/api/health").status_code == 200 and c.get("/api/ibm/status").status_code == 200
            return ok, (time.perf_counter() - t) * 1000

        base = [health(0)[1] for _ in range(50)]
        for label, fn, timeout_s, expect in (("error", failing, 10.0, 502), ("hang", hanging, 1.0, 504)):
            ibm._service = fn
            ibm.CHECK_TIMEOUT_S = timeout_s
            with cf.ThreadPoolExecutor(24) as ex:
                checks = [ex.submit(lambda: c.post("/api/ibm/check")) for _ in range(12)]
                hs = list(ex.map(health, range(120)))
                rs = [f.result() for f in checks]
            out[label] = {"check_codes": sorted({r.status_code for r in rs}), "expected": expect,
                          "leaked_token": any(TOKEN in r.text for r in rs), "health_ok": all(h[0] for h in hs),
                          "health_ms_p95": pct([h[1] for h in hs], 0.95)}
        out["health_ms_p95_baseline"] = pct(base, 0.95)
        out["ok"] = all(out[k]["check_codes"] == [out[k]["expected"]] and not out[k]["leaked_token"] and out[k]["health_ok"]
                        for k in ("error", "hang"))
    shutil.rmtree(d, ignore_errors=True)
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
