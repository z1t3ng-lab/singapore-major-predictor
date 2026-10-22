"""Desktop-only API on a private Unix socket, owned by the desktop supervisor.

The normal browser API and its database/model code are unchanged. Closing the
parent pipe also stops this server if the native window or Node process crashes.
"""
from pathlib import Path
import sys
import threading

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main() -> None:
    server = uvicorn.Server(uvicorn.Config(
        "app.main:app", uds=sys.argv[1], log_level="warning",
        timeout_graceful_shutdown=3, access_log=False,
    ))

    def parent_closed() -> None:
        sys.stdin.buffer.read()
        server.should_exit = True

    threading.Thread(target=parent_closed, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
