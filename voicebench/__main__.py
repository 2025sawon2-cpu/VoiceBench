from __future__ import annotations

import argparse

from .api import run_server


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceBench local benchmark harness")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    arguments = parser.parse_args()
    run_server(arguments.host, arguments.port)


if __name__ == "__main__":
    main()
