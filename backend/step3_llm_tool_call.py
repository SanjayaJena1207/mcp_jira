"""
Step 3: let the LLM choose an MCP tool, run it, and answer with the result.

Run from the backend folder (with the venv activated, and Ollama running):
    python step3_llm_tool_call.py 2>$null

This joins the two halves of the project:
    MCP server  -> knows how to talk to Jira (the tools)
    Ollama LLM  -> understands the question and decides which tool to use
Our script sits in the middle and passes messages between them. The LLM never
talks to MCP directly: it only *asks* for a tool call, and we execute it.
"""

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.stdio import stdio_client
from ollama import AsyncClient

# Importing this loads backend/.env and gives us the shared launch settings.
from mcp_server_config import get_server_params

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "DEMO")
# QUESTION = f"Show me the open issues in project {PROJECT_KEY}"
QUESTION = f"list issues assigned to me in project {PROJECT_KEY} and add a comment to each one saying 'I am working on this'"

# Safety cap for the tool-calling loop (see Step 5).
MAX_ROUNDS = 5

# Why we only give the model a few tools:
# - mcp-atlassian offers 63 tools. Every tool's name, description and input
#   schema is sent to the model as text in *every* request. 63 tools is many
#   thousands of tokens, which makes a local model slow and can push the actual
#   question out of its context window.
# - A small model (8B parameters) gets confused when many tools look alike
#   (jira_search vs jira_get_project_issues vs jira_get_board_issues ...).
#   It picks the wrong tool or invents arguments more often.
# - Fewer tools also means fewer dangerous ones (e.g. jira_delete_issue)
#   that the model could call by mistake.
# So we keep only what this demo needs: search, read, create, comment, and
# change status.
ALLOWED_TOOLS = {
    "jira_search",
    "jira_get_issue",
    "jira_create_issue",
    "jira_add_comment",
    "jira_get_transitions",
    "jira_transition_issue",
}


def mcp_tool_to_ollama(tool) -> dict:
    """Convert one MCP tool into Ollama's function-calling format.

    MCP and Ollama describe tools almost the same way, so this is a simple
    re-packaging: the MCP input_schema (a JSON Schema) becomes "parameters".
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.input_schema,
        },
    }


def tool_result_to_text(result) -> str:
    """Turn an MCP tool result into one string the LLM can read."""
    # The result's content is a list of items; we keep the text ones.
    text = "\n".join(item.text for item in result.content if item.type == "text")
    # A failed tool is a normal result with is_error=True (not an exception).
    # We still pass it to the LLM, so it can tell the user what went wrong.
    if result.is_error:
        return f"Error from tool: {text}"
    return text


async def main():
    # --- Step 1: connect to the MCP server and list its tools ----------------
    async with stdio_client(get_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()

            # --- Step 2: keep only the tools we allow -----------------------------
            mcp_tools = [t for t in tools_result.tools if t.name in ALLOWED_TOOLS]
            print(f"Giving the model {len(mcp_tools)} of {len(tools_result.tools)} tools: "
                  f"{', '.join(t.name for t in mcp_tools)}\n")

            # --- Step 3: convert them to Ollama's format -----------------------
            ollama_tools = [mcp_tool_to_ollama(t) for t in mcp_tools]

            # --- Step 4: ask the model the question, offering the tools ----------
            # The conversation is a list of messages. The model sees all of it
            # on every call; it has no memory of its own between calls.
            messages = [
                {
                    "role": "system",
                    "content": "You are a helpful Jira assistant. Use the available "
                               "tools to look up or change Jira data. Base your answers "
                               "only on the tool results.",
                },
                {"role": "user", "content": QUESTION},
            ]
            print(f"Question: {QUESTION}\n")

            ollama = AsyncClient()

            # --- Step 5: the tool-calling loop -----------------------------------
            # Some questions need several rounds of tools. Example: "list my
            # issues and comment on each one" needs jira_search first (to learn
            # the issue keys), and only then jira_add_comment for each key.
            # So we keep asking the model until it answers in plain text.
            # MAX_ROUNDS stops a confused model from calling tools forever.
            for round_number in range(1, MAX_ROUNDS + 1):
                print(f"--- Round {round_number}: asking the model ---\n")
                # think=False turns off qwen3's "thinking" text so we get a direct reply.
                response = await ollama.chat(
                    model=OLLAMA_MODEL,
                    messages=messages,
                    tools=ollama_tools,
                    think=False,
                )

                # The model either answers in text (it is done) or replies with
                # tool_calls: "please run tool X with arguments Y". It cannot run
                # anything itself.
                tool_calls = response.message.tool_calls or []
                if not tool_calls:
                    # --- Step 6: no more tools wanted -> this is the final answer
                    print("=== Final answer ===")
                    print(response.message.content)
                    return

                # Add the model's tool-call message to the conversation, so in
                # the next round it can see what it asked for.
                messages.append(response.message)

                # Execute each requested tool on the MCP server. The model may
                # ask for several at once (e.g. one jira_add_comment per issue).
                for call in tool_calls:
                    name = call.function.name
                    arguments = call.function.arguments
                    print(f"Model chose tool: {name}")
                    print(f"With arguments:   {json.dumps(arguments)}\n")

                    # This is the MCP "tools/call" request from step 2. Now the
                    # LLM chose the tool and arguments instead of us.
                    result = await session.call_tool(name, arguments)
                    result_text = tool_result_to_text(result)
                    print(f"Tool result (first 500 characters):\n{result_text[:500]}\n")

                    # Give the result back to the model as a "tool" message.
                    # tool_name tells it which of its tool calls this answers.
                    messages.append({
                        "role": "tool",
                        "tool_name": name,
                        "content": result_text,
                    })

                # Loop back: the model now sees the results and decides whether
                # it needs more tools or can answer.

            # We only get here if the model was still asking for tools after
            # MAX_ROUNDS rounds.
            print(f"Stopped after {MAX_ROUNDS} rounds: the model kept asking for tools.")


if __name__ == "__main__":
    asyncio.run(main())
