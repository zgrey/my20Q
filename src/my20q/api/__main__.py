"""Run the cockpit API:  python -m my20q.api

Overrides:
  MY20Q_API_HOST                  host to bind (default 127.0.0.1)
  MY20Q_API_PORT                  port to bind (default 8000)
  MY20Q_API_GRACEFUL_TIMEOUT      seconds uvicorn waits for in-flight
                                  requests to drain on shutdown (default 5)

For dev reload:  uvicorn my20q.api.app:create_app --factory --reload

Shutdown behaviour
------------------
A first Ctrl+C triggers a graceful shutdown — uvicorn stops accepting
new connections, the lifespan hook pushes a sentinel into every SSE
subscriber queue (so each generator wakes and exits its loop), and the
event loop drains. If anything is still in flight after
``MY20Q_API_GRACEFUL_TIMEOUT`` seconds, uvicorn force-closes the
remaining connections — so Ctrl+C always returns the terminal within a
few seconds, even with open EventSource streams.

A second Ctrl+C bypasses the wait entirely (uvicorn's built-in
force-exit).
"""

from __future__ import annotations

import logging
import os


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    host = os.environ.get("MY20Q_API_HOST", "127.0.0.1")
    port = int(os.environ.get("MY20Q_API_PORT", "8000"))
    graceful = int(os.environ.get("MY20Q_API_GRACEFUL_TIMEOUT", "5"))

    from my20q.api.app import create_app

    uvicorn.run(
        create_app(),
        host=host,
        port=port,
        # Backstop: even if a generator stalls past the lifespan sentinel
        # for some reason, uvicorn force-closes after this many seconds.
        timeout_graceful_shutdown=graceful,
    )


if __name__ == "__main__":
    main()
