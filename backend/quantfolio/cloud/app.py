"""Cloud Run API application.

The same FastAPI app as the local one, with three differences:

* no job supervisor is started in this process (the worker is a Cloud Run Job);
* the routes in `cloud.routes` sit in front of the local ones, so runs and
  uploads go through Firestore and the private bucket (there is no sign-in:
  per-client limits use a salted hash of the client address);
* the interactive API docs and the built-in static web app are not served
  (Firebase Hosting serves the web app; this service answers `/api/**` only).
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import FastAPI

from ..api.app import create_app
from ..settings import Settings, get_settings
from .auth import ServiceAccountOidcVerifier
from .config import CloudConfig, load_cloud_config
from .jobstore import CloudJobStore
from .routes import CloudState, install

_REMOVED_PATHS = frozenset({"/api/docs", "/api/docs/oauth2-redirect", "/api/openapi.json", "/{path:path}"})


def build_state(settings: Settings, cfg: CloudConfig | None = None) -> CloudState:
    """Production wiring: Firestore, Cloud Storage, the Cloud Run Admin API and the sweep's OIDC check."""
    from .blobs import GcsBlobStore
    from .db import FirestoreDocStore
    from .launcher import CloudRunJobLauncher

    cfg = cfg or load_cloud_config(settings)
    sweep = None
    if cfg.sweep_sa and cfg.sweep_audience:
        sweep = ServiceAccountOidcVerifier(cfg.sweep_audience, cfg.sweep_sa)
    return CloudState(
        cfg=cfg,
        jobs=CloudJobStore(FirestoreDocStore(cfg.project, cfg.database), cfg, max_events_per_run=settings.max_events_per_run),
        blobs=GcsBlobStore(cfg.project, cfg.bucket),
        launcher=CloudRunJobLauncher(cfg.project, cfg.region, cfg.worker_job),
        sweep_verifier=sweep,
    )


def create_cloud_app(settings: Settings | None = None, state: CloudState | None = None) -> FastAPI:
    s = replace(settings or get_settings(), start_worker=False)
    app = create_app(s)
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) not in _REMOVED_PATHS]
    app.openapi_url = None
    app.docs_url = None
    install(app, state or build_state(s))
    return app


def app_from_env() -> FastAPI:  # pragma: no cover - uvicorn --factory entry
    return create_cloud_app()


__all__ = ["app_from_env", "build_state", "create_cloud_app"]
