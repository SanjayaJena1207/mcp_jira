# CLAUDE.md

This file gives Claude Code the context it needs to work in this repository.

## Project goal

A **demo chat app** where a **local LLM** answers questions about Jira and performs
Jira actions (search issues, create issues, add comments, transition status, ...)
by calling tools exposed by an **MCP server**.

- LLM: [Ollama](https://ollama.com), model **`qwen3:8b`** (supports tool calling).
- MCP server: the open-source **`mcp-atlassian`** package.
- The user is **learning MCP**. This is a teaching project first, a product second.

## How it works (the big picture)

```
 Browser (React)                 Backend (FastAPI)                     Local processes
 ───────────────                 ─────────────────                     ───────────────
 chat UI  ──POST /chat──▶  1. add user message to history
                           2. ollama.chat(model, messages, tools) ──▶  Ollama (qwen3:8b)
                           3. model replies with tool_calls?
                              yes → session.call_tool(name, args) ──▶  mcp-atlassian (stdio)
                                    append tool result to messages            │
                                    go back to step 2                         ▼
                              no  → final answer                          Jira Cloud REST API
          ◀──── JSON reply ─  4. return the assistant's text
```

Key MCP ideas this project demonstrates:

1. **Client ↔ server over stdio** – the backend *launches* the MCP server as a child
   process (`uvx mcp-atlassian`) and talks to it via stdin/stdout.
2. **Initialize handshake** – `ClientSession.initialize()` before anything else.
3. **Tool discovery** – `session.list_tools()` returns each tool's name, description
   and JSON Schema for its inputs.
4. **Tool schemas → LLM tools** – convert each MCP tool into Ollama's tool format
   (`{"type": "function", "function": {"name", "description", "parameters"}}`;
   `parameters` is the MCP tool's `input_schema`).
5. **Tool calling loop** – the LLM picks a tool, the backend executes it with
   `session.call_tool(name, arguments)`, the result goes back to the LLM as a
   `{"role": "tool", ...}` message, repeat until the LLM gives a plain answer.

## Tech stack

| Part      | Choice |
|-----------|--------|
| Backend   | Python, FastAPI, official `mcp` Python SDK, `ollama` Python package |
| Frontend  | React + Vite, **plain CSS** (no Tailwind, no UI component libraries) |
| MCP server| `mcp-atlassian`, launched with `uvx mcp-atlassian` over **stdio** |
| LLM       | Ollama running locally, model `qwen3:8b` |

## Folder layout

```
backend/    FastAPI app, MCP client, Ollama chat loop
frontend/   React + Vite chat UI
CLAUDE.md
README.md
```

Keep the structure flat and small. Suggested backend files (create only when needed):

- `backend/main.py` – FastAPI app, routes, startup/shutdown (lifespan).
- `backend/mcp_client.py` – start the MCP server, list tools, call tools.
- `backend/chat.py` – the Ollama tool-calling loop.
- `backend/requirements.txt`, `backend/.env.example`.

## Configuration

The MCP server is configured through environment variables, loaded from `backend/.env`:

```
JIRA_URL=https://your-company.atlassian.net
JIRA_USERNAME=you@example.com
JIRA_API_TOKEN=<create at https://id.atlassian.com/manage-profile/security/api-tokens>
JIRA_PROJECT_KEY=DEMO
OLLAMA_MODEL=qwen3:8b
```

- **Never commit `.env` or real tokens.** Commit only `backend/.env.example` with
  placeholder values, and make sure `.env` is in `.gitignore`.
- When launching the MCP server via `StdioServerParameters`, pass
  `env={**os.environ, "JIRA_URL": ..., ...}` – passing only the Jira vars drops `PATH`,
  and `uvx` will not be found (especially on Windows).
- **Windows fixes on this machine** (see `backend/step1_list_tools.py`): set
  `UV_NATIVE_TLS=true` in the server env (otherwise uvx fails with "invalid peer
  certificate"), and remove `SSLKEYLOGFILE` from it (otherwise the server crashes with
  "no OPENSSL_Applink"). Reuse the same env-building code everywhere the server starts.
- **`mcp` SDK is v2.x**: model fields are snake_case (`server_info`, `input_schema`,
  `next_cursor`), not the camelCase names in older docs and examples.

## Running locally (Windows / PowerShell)

Prerequisites: Python 3.10+, Node.js 18+, `uv` (provides `uvx`), Ollama.

```powershell
# 1. LLM
ollama pull qwen3:8b

# 2. Backend
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# 3. Frontend (new terminal)
cd frontend
npm install
npm run dev        # http://localhost:5173
```

Configure the Vite dev server to proxy `/api` to `http://localhost:8000` (or enable
CORS in FastAPI for `http://localhost:5173`) – pick one and document it in the code.

## Coding guidelines (important – the user is learning)

- **Simple over clever.** Prefer plain functions and straightforward control flow.
  No extra abstraction layers, frameworks, or design patterns "for later".
- **Comment generously.** Explain *why* each step exists, especially every MCP step
  (launching the server, `initialize`, `list_tools`, `call_tool`) and every step of
  the tool-calling loop. Number the steps in comments where it helps (`# Step 3: ...`).
- **Explain as you go.** When adding or changing code, tell the user in the chat
  what each piece does and how it fits the big picture above.
- **Small steps.** Build incrementally and verify each piece works before moving on
  (e.g. first: connect to MCP and print the tool list; then: one chat turn without
  tools; then: the full tool loop; then: the UI).
- **Minimal dependencies.** Backend: `fastapi`, `uvicorn`, `mcp`, `ollama`,
  `python-dotenv`. Frontend: React + Vite only.
- Log tool calls and their results to the console so the user can *see* MCP working.
  Showing tool calls in the chat UI (e.g. a small "🔧 called jira_search" line) is
  a nice teaching touch.

## Implementation notes / gotchas

- **Keep one MCP session alive** for the app's lifetime. Open it in FastAPI's
  `lifespan` handler (using `contextlib.AsyncExitStack` for `stdio_client` and
  `ClientSession`) instead of starting a new server process per request.
- **Use the async Ollama client** (`ollama.AsyncClient`) inside FastAPI routes.
- **Tool results** from `call_tool` are a list of content items; join the text items
  into a string before sending them back to the model.
- **Cap the tool loop** (e.g. max 5 iterations) so a confused model can't loop forever.
- **qwen3 "thinking"**: qwen3 may emit `<think>...</think>` reasoning. Either disable
  thinking (`think=False` in recent `ollama` versions) or strip it before showing the
  answer.
- **Too many tools hurts small models.** `mcp-atlassian` exposes many tools (Jira and
  Confluence). With an 8B model, consider passing only a handful of Jira tools to the
  LLM (filter by name in the backend), and keep descriptions as-is from the server.
- **Write actions are real.** Creating/updating issues changes the real Jira site –
  use a test project. A read-only mode is worth considering for demos.
- The chat history lives in memory (or is sent from the frontend each request);
  no database.

## Status

Only this file exists so far. Do not generate application code until the user asks.
