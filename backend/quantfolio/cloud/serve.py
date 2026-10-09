"""Cloud Run service entrypoint: `python -m quantfolio.cloud.serve`.

Listens on 0.0.0.0:$PORT (Cloud Run sets PORT), one process, JSON logs on
stderr (Cloud Logging parses them), and trusts the proxy headers of the Google
front end so request logs carry the real scheme and client address.
"""

from __future__ import annotations

import os
import sys

import uvicorn

from ..logging_utils import configure_logging
from ..settings import get_settings


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    port = int(os.environ.get("PORT") or "8080")
    uvicorn.run(
        "quantfolio.cloud.app:app_from_env",
        factory=True,
        host="0.0.0.0",
        port=port,
        workers=1,
        log_config=None,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
        timeout_keep_alive=65,
        timeout_graceful_shutdown=8,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
