"""Run the cockpit API:  python -m my20q.api

Host/port override via MY20Q_API_HOST / MY20Q_API_PORT. For dev reload,
use:  uvicorn my20q.api.app:create_app --factory --reload
"""

from __future__ import annotations

import logging
import os


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    host = os.environ.get("MY20Q_API_HOST", "127.0.0.1")
    port = int(os.environ.get("MY20Q_API_PORT", "8000"))

    from my20q.api.app import create_app

    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
