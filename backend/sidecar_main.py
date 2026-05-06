from __future__ import annotations

import multiprocessing
import os

import uvicorn


def main() -> None:
    multiprocessing.freeze_support()
    os.environ.setdefault("HEIMGEIST_BACKEND_SIDECAR", "1")
    host = os.getenv("HEIMGEIST_BACKEND_HOST", "127.0.0.1")
    port = int(os.getenv("HEIMGEIST_BACKEND_PORT", "8000"))
    log_level = os.getenv("HEIMGEIST_BACKEND_LOG_LEVEL", "info")

    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
