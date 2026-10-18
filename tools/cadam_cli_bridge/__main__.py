"""``python -m tools.cadam_cli_bridge --config bridge.json``: serve until interrupted."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .server import load_config, make_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cadam_cli_bridge", description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--port", type=int, help="override the config port")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    config = load_config(args.config)
    if args.port is not None:
        config.port = args.port
    server = make_server(config)
    logging.info("CADAM CLI bridge on http://%s:%s (%d models; prompts are never logged)", config.host, server.server_address[1], len(config.models))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
