"""Production entrypoint: `python -m quantfolio.serve`.

Runs uvicorn bound to localhost, writes `data/state/api.pid`
(`{pid, create_time, port}`) so `scripts/stop.ps1` can stop exactly this
process, and watches for `data/state/api.stop` to shut down gracefully (which
also stops the job supervisor this process started).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time

import psutil

from .settings import get_settings

log = logging.getLogger("quantfolio.serve")


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m quantfolio.serve")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args(argv)
    if args.host:
        os.environ["QF_API_HOST"] = args.host
    if args.port:
        os.environ["QF_API_PORT"] = str(args.port)
    s = get_settings()

    from .logging_utils import configure_logging

    configure_logging(s.log_level, s.data_dir / "logs" / "api.log")
    if s.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("binding to a non-loopback address; the API has no authentication", extra={"host": s.host})
    if not port_free(s.host, s.port):
        print(f"Port {s.port} on {s.host} is already in use. Stop the other server or set QF_API_PORT.", file=sys.stderr)
        return 4

    import uvicorn

    from .api.app import create_app

    s.state_dir.mkdir(parents=True, exist_ok=True)
    pid_file = s.state_dir / "api.pid"
    stop_file = s.state_dir / "api.stop"
    try:
        stop_file.unlink()
    except FileNotFoundError:
        pass
    me = psutil.Process()
    pid_file.write_text(json.dumps({"pid": me.pid, "create_time": me.create_time(), "port": s.port, "host": s.host}), encoding="utf-8")

    config = uvicorn.Config(create_app(s), host=s.host, port=s.port, log_config=None, access_log=False, timeout_graceful_shutdown=8,
                            proxy_headers=False, server_header=False, date_header=True)
    server = uvicorn.Server(config)

    def watch_stop() -> None:
        while not server.should_exit:
            if stop_file.exists():
                log.info("stop requested via stop file")
                server.should_exit = True
                break
            time.sleep(0.5)

    threading.Thread(target=watch_stop, name="stop-file-watch", daemon=True).start()
    print(f"QuantFolio running at http://{s.host}:{s.port}  (API docs: http://{s.host}:{s.port}/api/docs). Press Ctrl+C to stop.",
          flush=True)
    try:
        server.run()
    finally:
        for f in (pid_file, stop_file):
            try:
                if f == pid_file:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    if data.get("pid") != me.pid:
                        continue
                f.unlink()
            except (FileNotFoundError, ValueError, OSError):
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
