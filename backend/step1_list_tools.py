"""
Step 1: connect to the mcp-atlassian MCP server and list its tools.

Run from the backend folder (with the venv activated):
    python step1_list_tools.py

No LLM is involved yet. This script only shows the MCP "plumbing":
launch the server, connect over stdio, shake hands, and ask what tools it has.
"""

import asyncio

from mcp import ClientSession
from mcp.client.stdio import stdio_client

# --- Steps 1 and 2: load .env and describe how to launch the server ---------
# Importing mcp_server_config loads backend/.env into os.environ.
# get_server_params() returns the command ("uvx mcp-atlassian") and the
# environment for the child process (process launch).
# See mcp_server_config.py for the details.
from mcp_server_config import get_server_params


async def main():
    server_params = get_server_params()

    # --- Step 3a: start the process and connect the stdio transport --------
    # stdio_client() launches the process and gives us two streams:
    #   read  -> messages the server writes to its stdout
    #   write -> messages we send to the server's stdin
    # MCP messages are JSON-RPC, one JSON object per line on these pipes.
    # Leaving the "async with" block stops the server process.
    async with stdio_client(server_params) as (read, write):

        # --- Step 3b: wrap the streams in an MCP session ---------------------
        # ClientSession handles the JSON-RPC details for us: it matches
        # requests to responses and turns them into Python objects.
        async with ClientSession(read, write) as session:

            # --- Step 3c: the handshake (initialize) --------------------------
            # The first message in every MCP connection. Client and server
            # exchange protocol versions and capabilities (e.g. "I have tools").
            # No other request is allowed before this succeeds.
            init_result = await session.initialize()
            # (The mcp SDK v2 uses snake_case names: server_info, input_schema.)
            print(f"Connected to MCP server: {init_result.server_info.name} "
                  f"(version {init_result.server_info.version})\n")

            # --- Step 3d: tool discovery (tools/list) -------------------------
            # Ask the server which tools it offers. Each tool has a name,
            # a description (written for the LLM to read), and an input_schema
            # (JSON Schema describing its arguments). Later we hand exactly
            # this information to the LLM so it can decide which tool to call.
            tools_result = await session.list_tools()
            tools = tools_result.tools

            # --- Step 4: print what we found -----------------------------------
            print(f"The server offers {len(tools)} tools:\n")
            for tool in tools:
                # Descriptions can be long; show only the first line.
                description = (tool.description or "").strip()
                first_line = description.splitlines()[0] if description else "(no description)"
                print(f"- {tool.name}: {first_line}")


if __name__ == "__main__":
    asyncio.run(main())
