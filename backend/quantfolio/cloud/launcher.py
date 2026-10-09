"""Starts one execution of the worker Cloud Run Job (IAM-authorised call to the Cloud Run Admin API).

The API's service account needs only `roles/run.invoker` on that one job
(`run.jobs.run`); no overrides are sent, so the job always runs exactly the
image, command, CPU, memory and timeout it was deployed with.
"""

from __future__ import annotations

from typing import Any, Protocol


class LaunchError(RuntimeError):
    pass


class JobLauncher(Protocol):
    def run(self) -> str | None: ...


class FakeLauncher:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def run(self) -> str | None:
        if self.fail:
            raise LaunchError("simulated launch failure")
        self.calls += 1
        return f"fake-execution-{self.calls}"


class CloudRunJobLauncher:
    def __init__(self, project: str, region: str, job: str) -> None:
        self.url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job}:run"
        self._session: Any = None

    def _authorized_session(self) -> Any:
        if self._session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            self._session = AuthorizedSession(creds)
        return self._session

    def run(self) -> str | None:
        try:
            resp = self._authorized_session().post(self.url, json={}, timeout=20)
        except Exception as exc:  # network, credentials
            raise LaunchError(f"{type(exc).__name__}: {exc}"[:300]) from exc
        if resp.status_code >= 300:
            raise LaunchError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            body = resp.json()
        except ValueError:
            return None
        return str((body.get("metadata") or {}).get("name") or body.get("name") or "") or None
