"""
Step 2: call one MCP tool (jira_search) directly, without an LLM.

Run from the backend folder (with the venv activated):
    python step2_call_tool.py

This is exactly what the LLM will do later, except that here *we* choose the
tool and its arguments by hand. Unlike list_tools, call_tool makes the MCP
server contact Jira, so bad settings (URL, token, project key) show up here.
"""

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.stdio import stdio_client

# Importing this loads backend/.env and gives us the shared launch settings.
from mcp_server_config import get_server_params

TOOL_NAME = "jira_search"

# JIRA_PROJECT_KEY is our own setting (mcp-atlassian never reads it).
# We use it to build the JQL query: "project = DEMO ORDER BY created DESC".
PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "DEMO")
TOOL_ARGUMENTS = {
    "jql": f"project = {PROJECT_KEY} ORDER BY created DESC",
    "limit": 5,
}


async def main():
    # --- Step 1: launch the server and connect over stdio ------------------
    # Same as step 1: start "uvx mcp-atlassian" as a child process and
    # connect to its stdin/stdout.
    async with stdio_client(get_server_params()) as (read, write):
        async with ClientSession(read, write) as session:

            # --- Step 2: the handshake ----------------------------------------
            # Always the first request in an MCP connection.
            await session.initialize()

            # --- Step 3: look at the tool's input schema (tools/list) ----------
            # Before calling a tool, a client (or an LLM) needs to know which
            # arguments it accepts. That is the tool's input_schema: a JSON
            # Schema listing each argument, its type, a description, and which
            # arguments are required.
            tools_result = await session.list_tools()
            tool = next((t for t in tools_result.tools if t.name == TOOL_NAME), None)
            if tool is None:
                print(f"The server has no tool named {TOOL_NAME!r}.")
                return

            print(f"=== Input schema of {TOOL_NAME} ===")
            print(json.dumps(tool.input_schema, indent=2))
            print()

            # --- Step 4: call the tool (tools/call) -----------------------------
            # We send the tool name and a dict of arguments. The server checks
            # them against the input schema, runs its Python function, which
            # calls the Jira REST API at JIRA_URL, and sends back the result.
            print(f"=== Calling {TOOL_NAME} with {TOOL_ARGUMENTS} ===")
            result = await session.call_tool(TOOL_NAME, TOOL_ARGUMENTS)

            # --- Step 5: read the result ---------------------------------------
            # A tool result has:
            #   is_error -> True if the tool failed (e.g. Jira rejected the JQL).
            #               A failed tool is a normal result, not a Python
            #               exception, so we must check this ourselves.
            #   content  -> a list of content items. Usually one TextContent
            #               item whose .text is the tool's output (here, JSON).
            print(f"is_error: {result.is_error}")
            print(f"content items: {len(result.content)}\n")

            for item in result.content:
                if item.type == "text":
                    print(item.text)
                else:
                    # Other content types (e.g. images) exist too.
                    print(f"[{item.type} content]")


if __name__ == "__main__":
    asyncio.run(main())
