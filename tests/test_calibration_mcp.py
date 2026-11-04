import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_calibration_stdio_contract():
    async def check():
        params = StdioServerParameters(
            command=sys.executable,
            env={"PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
            args=["-c", "from hxaudio_mcp.server import run; run()"],
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                listed = await session.list_tools()
                tools = {tool.name: tool.input_schema for tool in listed.tools}
                assert {"calibration_" + name for name in (
                    "check", "measure", "optimize", "run", "status", "cancel", "report"
                )} <= tools.keys()
                assert tools["calibration_check"]["properties"]["peak"]["type"] == "number"
                result = await session.call_tool("calibration_status", {"session_id": "../x"})
                assert json.loads(result.content[0].text)["error"]["code"] == "invalid_input"

    asyncio.run(asyncio.wait_for(check(), 20))
