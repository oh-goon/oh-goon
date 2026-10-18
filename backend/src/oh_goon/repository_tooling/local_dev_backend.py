from __future__ import annotations

import argparse


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the contributor-local Oh-Goon backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--reload-dir", action="append", default=[])
    parser.add_argument("--log-level", default="info")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    import uvicorn

    uvicorn.run(
        "oh_goon.api.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=args.reload_dir or None,
        log_level=args.log_level,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
