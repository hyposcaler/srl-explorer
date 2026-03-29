# Task: Add Agent Reasoning Step and Improve Tool Selection

## Overview

Two related changes to srl-explorer:

1. **Visible reasoning step**: The agent should think through its approach before calling tools, and that reasoning should be displayed in the REPL.
2. **Smarter tool selection**: Tighten the system prompt so the agent reasons clearly about when to use gnmic (current state) vs Prometheus (historical data) vs yang_search (path discovery), and plans before acting.

## Change 1: Agent Reasoning via Structured Output

### How it works

Before the agent calls any tools, it should first produce a reasoning/planning response that explains:
- What the user is asking for
- What information is needed to answer
- Which tool(s) are appropriate and why
- The order of operations if chaining tools

### Implementation approach

Add a `reasoning` field to the system prompt instructions. Tell the LLM:

> Before calling any tools, always first respond with your reasoning about how to answer the question. Wrap your reasoning plan in <reasoning>...</reasoning> tags. Consider:
> - Is this asking about current/live state, or historical trends?
> - Do I know the exact YANG path, or do I need to discover it first?
> - Which device(s) need to be queried?
> - Should I use one tool or chain multiple tools?
> - For counters and rates, should I use Prometheus (preferred for time-series) or gnmic (for instantaneous values)?
>
> After your reasoning, proceed with tool calls. On subsequent tool call rounds (after receiving tool results), you do NOT need to reason again — just continue with the next tool call or synthesize your final answer.

### REPL display

In `cli.py`, parse the assistant's content for `<reasoning>...</reasoning>` tags. When found:
- Display the reasoning text prefixed with `>>> ` in dim styling, similar to tool calls
- Strip the reasoning tags from what gets displayed as the final answer (if there's other content alongside it)

Add an `on_reasoning` callback to the Agent class, similar to `on_tool_call`:

```python
on_reasoning: Callable[[str], None] | None = None
```

In the agent loop, after each LLM response, check if `msg.content` contains `<reasoning>...</reasoning>`. If so, extract it and call `on_reasoning` with the text. This should happen before tool call processing.

In `cli.py`:

```python
def _on_reasoning(text: str) -> None:
    for line in text.strip().splitlines():
        console.print(f"  [dim]>>> {line}[/dim]")
```

### Logging

Log the reasoning as its own file in the turn directory:

```
turn_001/
  00_user_message.json
  01_llm_reasoning.json      # NEW
  02_tool_call_yang_search.json
  ...
```

Schema:

```json
{
  "type": "reasoning",
  "timestamp": "2026-03-29T12:00:02.000Z",
  "content": "The user is asking about current BGP neighbor state..."
}
```

## Change 2: Improved Tool Selection in System Prompt

### Updated guidance in prompts.py

Replace the existing Tool Selection Guide section with more prescriptive reasoning-first guidance:

```
## Tool Selection — Think Before Acting

Before calling any tools, reason through your approach. Consider what the user is actually asking for and select tools accordingly.

### Decision Framework

1. **"What is the current state of X?"** → gnmic_get
   - Current config, operational state, admin status, neighbor state
   - Real-time values that don't need historical context
   - Examples: "show BGP neighbors", "is interface X up", "what's the hostname"

2. **"What happened over time?" / "Show me trends"** → prometheus_query
   - Historical data, trends, aggregations, comparisons over time
   - Rate calculations on counters (use rate() or irate() in PromQL)
   - Examples: "CPU usage over the last hour", "traffic trends on uplinks", "when did errors start increasing"

3. **"What path/metric exists for X?"** → yang_search
   - When you don't know the exact YANG path for a feature area
   - When verifying a path exists before querying
   - Always search before guessing an uncommon path

4. **Counter values specifically**:
   - For raw counter values right now → gnmic_get
   - For rates, deltas, or trends on counters → prometheus_query (strongly preferred — use rate(), irate(), increase())
   - Do NOT manually compute rates from gnmic counter snapshots

5. **Chaining tools**:
   - Unknown path → yang_search first, then gnmic_get or prometheus_query
   - Need both current state and trend → gnmic_get + prometheus_query
   - Verifying a config matches operational behavior → gnmic_get (config) + prometheus_query (observed metrics)

### Anti-patterns to avoid

- Do NOT query Prometheus for current/instantaneous state when gnmic gives you the live value directly
- Do NOT use gnmic to get historical trends — it only returns current state
- Do NOT guess YANG paths for uncommon features — use yang_search first
- Do NOT query all 5 devices when the user only asked about one
- Do NOT skip reasoning — always plan your approach before calling tools
```

### Keep the common paths and Prometheus metrics reference

The existing sections documenting common gNMI paths and Prometheus metric naming conventions should remain unchanged — they're reference material the LLM uses after it's decided which tool to use.

## Expected REPL Output

After these changes, a typical interaction should look like:

```
srl> is there any packet loss between leaf1 and spine1?

  >>> To check for packet loss between leaf1 and spine1, I need to look at
  >>> interface error/discard counters on the links connecting them.
  >>> From the topology: leaf1:e1-49 connects to spine1:e1-1.
  >>> I'll use Prometheus to check for error rate trends on both sides,
  >>> since rate calculations on counters are better suited to Prometheus
  >>> than raw gnmic snapshots.
  >>> gnmic_get(target=leaf1, path=/interface[name=ethernet-1/49]/statistics)
  >>> prometheus_query(query=rate(interface_statistics_in_error_packets{source=~"leaf1|spine1"}[5m]))

[agent's synthesized answer in markdown]
```

## Constraints

- The reasoning tags approach keeps it simple — no separate API call or chain-of-thought parameter needed
- Reasoning should only happen on the FIRST LLM response in a turn, not on subsequent rounds after tool results come back
- The on_reasoning callback follows the same pattern as on_tool_call and on_tool_result
- Logging captures reasoning as a distinct event type
- Do not change the tool definitions (function calling schemas) — only the system prompt and agent loop logic