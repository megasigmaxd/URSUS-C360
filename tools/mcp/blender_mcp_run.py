#!/usr/bin/env python3
"""Drive Blender through the blender-mcp MCP server (stdio) and its Blender add-on bridge.

Examples:
    python3 tools/mcp/blender_mcp_run.py --list-tools
    python3 tools/mcp/blender_mcp_run.py --script blender/build_all.py -- --out FS25_UrsusC360
    python3 tools/mcp/blender_mcp_run.py --screenshot docs/mcp_viewport.png

Requires the `mcp` Python package and a GUI Blender started with tools/mcp/start_blender_mcp.py.
The server command defaults to the local blender-mcp checkout; override with BLENDER_MCP_SERVER.
"""
import argparse
import asyncio
import base64
import os
import shlex
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

DEFAULT_SERVER = "uv run --quiet --directory /tmp/work/blender-mcp mcp-for-blender"


def _server_params():
    cmd = shlex.split(os.environ.get("BLENDER_MCP_SERVER", DEFAULT_SERVER))
    env = dict(os.environ, DISABLE_TELEMETRY="true", BLENDER_MCP_DISABLE_TELEMETRY="true")
    return StdioServerParameters(command=cmd[0], args=cmd[1:], env=env)


def _texts(result):
    return "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")


async def _run(args):
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if args.list_tools:
                tools = await session.list_tools()
                print("\n".join(t.name for t in tools.tools))
            if args.script:
                path = os.path.abspath(args.script)
                code = (
                    "import runpy, sys\n"
                    f"sys.argv = {[path] + args.script_args!r}\n"
                    f"runpy.run_path({path!r}, run_name='__main__')\n"
                )
                result = await session.call_tool("execute_blender_code", {"code": code, "user_prompt": ""})
                text = _texts(result)
                print(text)
                if getattr(result, "is_error", False) or text.startswith("Error executing code"):
                    return 1
            if args.screenshot:
                result = await session.call_tool("get_viewport_screenshot", {"max_size": 1400, "user_prompt": ""})
                images = [c for c in result.content if getattr(c, "type", "") == "image"]
                if not images:
                    print(_texts(result))
                    return 1
                with open(args.screenshot, "wb") as fh:
                    fh.write(base64.b64decode(images[0].data))
                print("screenshot:", args.screenshot)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list-tools", action="store_true")
    parser.add_argument("--script", help="Python file executed inside Blender via execute_blender_code")
    parser.add_argument("--screenshot", help="save a viewport screenshot (PNG) to this path")
    parser.add_argument("script_args", nargs="*", help="arguments after -- are passed as sys.argv[1:]")
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
