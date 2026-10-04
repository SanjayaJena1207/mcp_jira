import { useEffect, useRef, useState } from 'react'
import './App.css'

// The FastAPI backend (backend/main.py). We call it directly on its own port;
// the backend allows this through CORS for http://localhost:5173.
const CHAT_URL = 'http://localhost:8000/chat'

// Shown when the chat is empty. `writes` marks prompts that change real Jira data.
const SAMPLE_PROMPTS = [
  { text: 'List the open issues in project MYT', writes: false },
  { text: 'Explain what MYT-1 is about', writes: false },
  { text: 'Create a bug in MYT: "Login button does nothing on the settings page"', writes: true },
  { text: 'Add a comment to MYT-1 saying "Looking into this now"', writes: true },
  { text: 'Move MYT-1 to In Progress', writes: true },
]

function App() {
  // The whole conversation. Each item is { role, content } and assistant
  // messages also carry toolCalls: [{ name, args, ok }] from the backend.
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const bottomRef = useRef(null)

  // Keep the newest message in view.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  async function sendMessage(text) {
    const content = text.trim()
    if (!content || loading) return

    // Add the user's message right away so the UI feels responsive.
    const history = [...messages, { role: 'user', content }]
    setMessages(history)
    setInput('')
    setError('')
    setLoading(true)

    try {
      // The backend keeps no memory, so we send the full conversation every
      // time. Only role + content: the tool-call details are just for display.
      const response = await fetch(CHAT_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: history.map((m) => ({ role: m.role, content: m.content })),
        }),
      })
      if (!response.ok) {
        throw new Error(`The backend returned an error (HTTP ${response.status}). Check the uvicorn console.`)
      }

      const data = await response.json()
      setMessages([
        ...history,
        { role: 'assistant', content: data.reply, toolCalls: data.tool_calls },
      ])
    } catch (err) {
      // fetch() throws a TypeError when it cannot reach the server at all.
      if (err instanceof TypeError) {
        setError('Cannot reach the backend at http://localhost:8000. Is uvicorn running?')
      } else {
        setError(err.message)
      }
    } finally {
      setLoading(false)
    }
  }

  function handleKeyDown(event) {
    // Enter sends; Shift+Enter adds a new line.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      sendMessage(input)
    }
  }

  return (
    <div className="app">
      <header className="header">
        <h1>Jira assistant</h1>
        <p>A local LLM (Ollama) answers your questions by calling Jira tools through an MCP server.</p>
      </header>

      <main className="chat">
        {messages.length === 0 && (
          <div className="samples">
            <p className="samples-title">Try one of these:</p>
            {SAMPLE_PROMPTS.map((prompt) => (
              <button
                key={prompt.text}
                className="sample"
                onClick={() => sendMessage(prompt.text)}
                disabled={loading}
              >
                {prompt.text}
                {prompt.writes && <span className="writes-tag">writes to Jira</span>}
              </button>
            ))}
          </div>
        )}

        {messages.map((message, index) => (
          <div key={index} className={`message ${message.role}`}>
            <div className="bubble">{message.content}</div>

            {/* The MCP tools the model called to produce this answer. */}
            {message.toolCalls?.length > 0 && (
              <ul className="tool-calls">
                {message.toolCalls.map((call, i) => (
                  <li key={i}>
                    <span className={call.ok ? 'tool-name ok' : 'tool-name failed'}>
                      🔧 {call.name} {call.ok ? '✓' : '✗'}
                    </span>
                    <code className="tool-args">{JSON.stringify(call.args)}</code>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}

        {loading && <div className="status">Working on it…</div>}
        {error && <div className="error">{error}</div>}
        <div ref={bottomRef} />
      </main>

      <footer className="composer">
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your Jira issues… (Enter to send, Shift+Enter for a new line)"
          rows={2}
          disabled={loading}
        />
        <button onClick={() => sendMessage(input)} disabled={loading || !input.trim()}>
          Send
        </button>
      </footer>
    </div>
  )
}

export default App
