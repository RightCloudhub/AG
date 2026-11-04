"""Real stdio server with fake HTTP; subprocess does not open network sockets."""

import asyncio

import httpx
from ag_client import AsyncClient
from fake_api import FakeAPI

from ag_mcp.server import run_stdio


async def main():
    async with AsyncClient(
        api_key="test-key", kb_id="contracts", transport=httpx.MockTransport(FakeAPI().handle)
    ) as client:
        await run_stdio(client, kb_id="contracts")


asyncio.run(main())
