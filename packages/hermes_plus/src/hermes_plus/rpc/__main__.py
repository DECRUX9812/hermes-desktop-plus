"""Serve the plus RPC surface on stdin/stdout, one JSON-RPC message per line."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TextIO

from hermes_plus.rpc.context import RpcContext
from hermes_plus.rpc.registry import handle_text
from hermes_plus.store import open_store


async def serve(ctx: RpcContext, stdin: TextIO, stdout: TextIO) -> None:
    while True:
        line = await asyncio.to_thread(stdin.readline)
        if not line:
            return
        line = line.strip()
        if not line:
            continue
        reply = await handle_text(ctx, line)
        if reply is not None:
            stdout.write(reply + "\n")
            stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hermes_plus.rpc", description=__doc__)
    parser.add_argument("--plus-home", required=True, type=Path, help="directory that holds plus.db")
    args = parser.parse_args(argv)
    store = open_store(args.plus_home)
    try:
        asyncio.run(serve(RpcContext(store), sys.stdin, sys.stdout))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
