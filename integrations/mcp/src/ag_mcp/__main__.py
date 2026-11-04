"""Console entry point; stdout is reserved for JSON-RPC during execution."""

import argparse
import asyncio
import logging
import sys

from ag_client import AsyncClient
from pydantic import ValidationError

from ag_mcp.server import run_stdio
from ag_mcp.settings import Settings


async def serve(settings: Settings) -> None:
    key = settings.api_key.get_secret_value() if settings.api_key else None
    async with AsyncClient(
        settings.base_url,
        api_key=key,
        kb_id=settings.kb_id,
        timeout=settings.timeout_seconds,
    ) as client:
        await run_stdio(client, kb_id=settings.kb_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="AgenticGraphRAG stdio MCP server")
    parser.add_argument("--version", action="version", version="ag-mcp 0.1.0")
    parser.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        settings = Settings()
    except ValidationError:
        print(
            "Invalid AG_MCP configuration; check the documented environment settings",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    try:
        asyncio.run(serve(settings))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
