# Task: Add Structured Turn Logging to srl-explorer

## Overview

Add a logging system that captures full context from every agent interaction — user messages, LLM responses, tool calls, tool results, and token usage — organized into a directory hierarchy under `./logs/`.

## Directory Structure

```
./logs/
  session_2026-03-29T12-00-00/
    turn_001/
      00_user_message.json
      01_llm_response.json          # may include tool_calls
      02_tool_call_yang_search.json
      03_tool_result_yang_search.json
      04_tool_call_gnmic_get.json
      05_tool_result_gnmic_get.json
      06_llm_response_final.json
    turn_002/
      00_user_message.json
      01_llm_response_final.json    # no tools needed
    session_summary.json            # written/updated after each turn
```

- One directory per session, named with ISO timestamp (colons replaced with hyphens for filesystem safety)
- One subdirectory per user turn, zero-padded (`turn_001`, `turn_002`, ...)
- Files within a turn are sequenced with a zero-padded counter prefix and a descriptive name
- A `session_summary.json` at the session root tracks aggregate stats

## File Schemas

### 00_user_message.json

```json
{
  "type": "user_message",
  "timestamp": "2026-03-29T12:00:01.123Z",
  "content": "show me BGP neighbors on leaf1"
}
```

### NN_llm_response.json (intermediate, has tool_calls)

```json
{
  "type": "llm_response",
  "timestamp": "2026-03-29T12:00:02.456Z",
  "content": null,
  "tool_calls": [
    {
      "id": "call_abc123",
      "name": "gnmic_get",
      "arguments": {
        "target": "leaf1",
        "path": "/network-instance[name=default]/protocols/bgp/neighbor"
      }
    }
  ],
  "model": "gpt-4o",
  "usage": {
    "prompt_tokens": 1200,
    "completion_tokens": 45,
    "total_tokens": 1245
  },
  "finish_reason": "tool_calls"
}
```

### NN_tool_call_<name>.json

```json
{
  "type": "tool_call",
  "timestamp": "2026-03-29T12:00:02.500Z",
  "tool_call_id": "call_abc123",
  "name": "gnmic_get",
  "arguments": {
    "target": "leaf1",
    "path": "/network-instance[name=default]/protocols/bgp/neighbor"
  }
}
```

### NN_tool_result_<name>.json

```json
{
  "type": "tool_result",
  "timestamp": "2026-03-29T12:00:03.100Z",
  "tool_call_id": "call_abc123",
  "name": "gnmic_get",
  "duration_ms": 600,
  "success": true,
  "result": { "...": "full tool output, no truncation in logs" },
  "error": null
}
```

### NN_llm_response_final.json (terminal response, no tool_calls)

```json
{
  "type": "llm_response_final",
  "timestamp": "2026-03-29T12:00:04.789Z",
  "content": "Leaf1 has 2 BGP neighbors...",
  "tool_calls": null,
  "model": "gpt-4o",
  "usage": {
    "prompt_tokens": 2400,
    "completion_tokens": 150,
    "total_tokens": 2550
  },
  "finish_reason": "stop"
}
```

### session_summary.json

Updated after each turn completes:

```json
{
  "session_id": "session_2026-03-29T12-00-00",
  "started_at": "2026-03-29T12:00:00.000Z",
  "model": "gpt-4o",
  "turns": 5,
  "total_tool_calls": 12,
  "total_usage": {
    "prompt_tokens": 15000,
    "completion_tokens": 800,
    "total_tokens": 15800
  },
  "tool_call_counts": {
    "gnmic_get": 5,
    "prometheus_query": 4,
    "yang_search": 3
  },
  "errors": 1
}
```

## Implementation

### New file: `src/srl_explorer/logging.py`

Create a `TurnLogger` class:

- Initialized with a session directory path (created once at REPL startup)
- `start_turn()` — creates the next `turn_NNN/` subdirectory, resets the file sequence counter
- `log_user_message(content)` — writes the user message file
- `log_llm_response(message, usage, finish_reason, model)` — writes an LLM response file; marks it `_final` if finish_reason is `stop`
- `log_tool_call(tool_call_id, name, arguments)` — writes a tool call file
- `log_tool_result(tool_call_id, name, result, duration_ms, error)` — writes a tool result file; stores the FULL result (no truncation — truncation is only for the LLM context window, not for logs)
- `update_session_summary()` — called at end of each turn, updates the session-level summary file
- Each `log_*` method increments the sequence counter and writes synchronously (logging should not be async — it's fast local I/O and must not be lost)

### Changes to `agent.py`

- Accept a `TurnLogger` instance (optional, for testability)
- In `chat()`:
  - Call `logger.start_turn()` at the top
  - Log the user message
  - After each `client.chat.completions.create()` call, log the LLM response with `response.usage` data
  - Before each tool execution, log the tool call
  - After each tool execution, log the tool result with timing (`time.monotonic()` around execution) and full untruncated result
  - After the final response, call `logger.update_session_summary()`
- The existing `on_tool_call` / `on_tool_result` callbacks for Rich display remain unchanged — logging is a separate concern

### Changes to `cli.py`

- At REPL startup, create the session directory: `./logs/session_{timestamp}/`
- Instantiate `TurnLogger` with the session path
- Pass it to `Agent`
- The `LOGS_DIR` should be configurable via env var `SRL_EXPLORER_LOGS_DIR` (default `./logs`), add to `config.py`

### Changes to `config.py`

- Add `logs_dir: Path` field, default `Path("./logs")`, sourced from `SRL_EXPLORER_LOGS_DIR` env var

## Constraints

- All JSON files should be written with `indent=2` for readability
- Use `datetime.utcnow().isoformat() + "Z"` for timestamps
- Tool results in log files must NOT be truncated — the full response is logged even though the agent truncates what goes into the LLM context
- Logging failures should be caught and printed as warnings, never crash the REPL
- No new dependencies — just `json`, `pathlib`, `time`, `datetime` from stdlib