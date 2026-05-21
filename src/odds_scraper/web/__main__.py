from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from ..config import load_config
from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="odds-scraper web UX — read-only consumer of data/odds.db",
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    cfg = load_config(args.config)
    db_path = Path(cfg.output.db_path)
    if not db_path.exists():
        raise SystemExit(
            f"db not found at {db_path}; run the scraper first to create it",
        )
    app = create_app(db_path=db_path)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
