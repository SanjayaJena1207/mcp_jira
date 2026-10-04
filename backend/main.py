"""
The FastAPI backend: a chat endpoint where a local LLM answers Jira questions
by calling tools on the mcp-atlassian MCP server.

Run from the backend folder (with the venv activated, and Ollama running):
    uvicorn main:app --reload --port 8000

Then try:
    GET  http://localhost:8000/health
    POST http://localhost:8000/chat   {"messages": [{"role": "user", "content": "..."}]}

This is step3_llm_tool_call.py turned into a web app. The differences:
- The MCP server starts ONCE when the app starts (not once per question), and
  the same session is shared by every request.
- The question comes from the request body instead of a constant, and the
  frontend sends the whole chat history each time (the backend stores nothing).
"""

import json
import os
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from ollama import AsyncClient
from pydantic import BaseModel

# Importing this loads backend/.env and gives us the shared launch settings.
from mcp_server_config import get_server_params

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b")
PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "DEMO")

# Safety cap for the agent loop: a confused model can't call tools forever.
MAX_ROUNDS = 6

# Same whitelist as step3: a small model does much better with a few tools
# than with all 63 that mcp-atlassian offers (see step3 for the full reasoning).
ALLOWED_TOOLS = {
    "jira_search",
    "jira_get_issue",
    "jira_create_issue",
    "jira_add_comment",
    "jira_get_transitions",
    "jira_transition_issue",
}

# The system prompt is the model's standing instructions. Each line exists
# because an 8B model tends to get that particular thing wrong without it.
SYSTEM_PROMPT = f"""You are a helpful Jira assistant. Use the available tools to look up or change Jira data.
Rules:
- If the user does not name a project, use project key {PROJECT_KEY}.
- To find issues, use jira_search with a JQL query, for example:
  project = {PROJECT_KEY} AND assignee = currentUser() AND statusCategory != Done
- Before changing an issue's status, call jira_get_transitions to get the valid
  transition ids, then call jira_transition_issue with one of those ids.
- Only create, comment on, or transition issues when the user explicitly asks you to.
- Base your answers only on the tool results. If a tool fails, say so."""


# --- Helpers (same as step3) -------------------------------------------------

def mcp_tool_to_ollama(tool) -> dict:
    """Convert one MCP tool into Ollama's function-calling format.

    The MCP input_schema (a JSON Schema) becomes Ollama's "parameters".
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
    """Turn an MCP tool result (a list of content items) into one string."""
    text = "\n".join(item.text for item in result.content if item.type == "text")
    # A failed tool is a normal result with is_error=True (not an exception).
    if result.is_error:
        return f"Error from tool: {text}"
    return text


# --- Startup / shutdown: one MCP session for the app's whole life ------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once: the code before `yield` at startup, the code after at shutdown.

    Why AsyncExitStack: stdio_client and ClientSession are both "async with"
    context managers. In the step scripts we nested them around our code, but
    here the code that uses them (the routes) runs later, in other functions.
    The exit stack enters them now and keeps them open until we close it.
    """
    async with AsyncExitStack() as stack:
        # Step 1: launch the MCP server as a child process and connect to its
        # stdin/stdout.
        read, write = await stack.enter_async_context(stdio_client(get_server_params()))

        # Step 2: open an MCP session over that connection and do the
        # initialize handshake (protocol version + capabilities).
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        # Step 3: discover the tools, keep only the whitelisted ones, and
        # convert them to Ollama's format. Tools don't change while the app
        # runs, so we do this once instead of on every request.
        tools_result = await session.list_tools()
        mcp_tools = [t for t in tools_result.tools if t.name in ALLOWED_TOOLS]
        print(f"MCP ready. Giving the model {len(mcp_tools)} of "
              f"{len(tools_result.tools)} tools: {', '.join(t.name for t in mcp_tools)}")

        # app.state is FastAPI's place to share objects with the routes.
        app.state.session = session
        app.state.ollama_tools = [mcp_tool_to_ollama(t) for t in mcp_tools]
        app.state.tool_names = [t.name for t in mcp_tools]
        app.state.ollama = AsyncClient()

        yield  # The app serves requests while we are paused here.

    # Leaving the "async with" closes the session and stops the MCP server.
    print("MCP session closed.")


app = FastAPI(lifespan=lifespan)

# The React dev server runs on a different port (5173), so the browser treats
# it as a different origin. CORS tells the browser it may call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request body shapes -----------------------------------------------------

class ChatMessage(BaseModel):
    role: str      # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    # The full chat history so far. The backend keeps no memory, so the
    # frontend sends everything on every request.
    messages: list[ChatMessage]


# --- Routes -------------------------------------------------------------------

@app.get("/health")
async def health():
    """Quick check that the app is up and MCP is connected."""
    return {"model": OLLAMA_MODEL, "tools": app.state.tool_names}


@app.post("/chat")
async def chat(request: ChatRequest):
    session: ClientSession = app.state.session
    ollama: AsyncClient = app.state.ollama

    # Agent step A: build the conversation. The system prompt goes first, then
    # the chat history from the frontend. This list grows as tools are called.
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += [m.model_dump() for m in request.messages]

    # Every tool call is recorded here so the UI can show "🔧 called jira_search".
    tool_log = []

    for round_number in range(1, MAX_ROUNDS + 1):
        # Agent step B: ask the model. It sees the whole conversation (including
        # earlier tool results) plus the tool list, and decides what to do next.
        # think=False turns off qwen3's "thinking" text so we get a direct reply.
        print(f"\n--- Round {round_number}: asking {OLLAMA_MODEL} ---")
        response = await ollama.chat(
            model=OLLAMA_MODEL,
            messages=messages,
            tools=app.state.ollama_tools,
            think=False,
        )

        # Agent step C: no tool calls means the model is done -> final answer.
        tool_calls = response.message.tool_calls or []
        if not tool_calls:
            print(f"Final answer: {response.message.content}")
            return {"reply": response.message.content, "tool_calls": tool_log}

        # Agent step D: remember the model's request in the conversation, so in
        # the next round it can see which tools it asked for.
        messages.append(response.message)

        # Agent step E: run each requested tool on the MCP server. The model may
        # ask for several at once (e.g. one jira_add_comment per issue).
        for call in tool_calls:
            name = call.function.name
            args = call.function.arguments or {}
            print(f"Tool call: {name} {json.dumps(args)}")

            if name not in ALLOWED_TOOLS:
                # The model invented a tool name. Tell it, instead of crashing.
                result_text, ok = f"Error: unknown tool '{name}'.", False
            else:
                try:
                    # The MCP "tools/call" request.
                    result = await session.call_tool(name, args)
                    result_text, ok = tool_result_to_text(result), not result.is_error
                except Exception as error:
                    # E.g. arguments that don't match the tool's schema. The
                    # model gets the error and can try again with better args.
                    result_text, ok = f"Error calling tool: {error}", False

            print(f"  -> {'ok' if ok else 'FAILED'}: {result_text[:300]}")
            tool_log.append({"name": name, "args": args, "ok": ok})

            # Agent step F: hand the result back to the model as a "tool"
            # message, then loop to step B.
            messages.append({"role": "tool", "tool_name": name, "content": result_text})

    # The model was still asking for tools after MAX_ROUNDS rounds.
    return {
        "reply": f"Sorry, I stopped after {MAX_ROUNDS} rounds of tool calls without a final answer.",
        "tool_calls": tool_log,
    }
