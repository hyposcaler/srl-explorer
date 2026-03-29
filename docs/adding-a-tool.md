# How to Add a New Tool

This guide walks through adding a new tool to srl-explorer, using a hypothetical `config_diff` tool as a concrete example. This tool would compare the running config between two devices.

## 1. Write the tool function

Create a new file in `src/srl_explorer/tools/`:

```python
# src/srl_explorer/tools/config_diff.py
from __future__ import annotations

import asyncio
import json

from srl_explorer.config import Config, TOPOLOGY, CREDENTIALS


async def config_diff(config: Config, device_a: str, device_b: str, path: str) -> dict:
    """Fetch config from two devices via gnmic and return the diff."""
    async def _get_config(device: str) -> dict:
        addr = TOPOLOGY[device]["address"]
        proc = await asyncio.create_subprocess_exec(
            "gnmic", "-a", addr,
            "-u", CREDENTIALS["username"],
            "-p", CREDENTIALS["password"],
            "--skip-verify", "-e", "json_ietf",
            "get", "--path", path, "--type", "CONFIG",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode != 0:
            raise RuntimeError(f"gnmic error on {device}: {stderr.decode()}")
        return json.loads(stdout)

    config_a, config_b = await asyncio.gather(
        _get_config(device_a),
        _get_config(device_b),
    )

    return {
        "device_a": {"name": device_a, "config": config_a},
        "device_b": {"name": device_b, "config": config_b},
        "match": config_a == config_b,
    }
```

## 2. Add the OpenAI function definition

In `src/srl_explorer/prompts.py`, append to the `TOOLS` list:

```python
{
    "type": "function",
    "function": {
        "name": "config_diff",
        "description": (
            "Compare the running configuration of two devices at a given "
            "gNMI path. Returns both configs and whether they match."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "device_a": {
                    "type": "string",
                    "enum": ["leaf1", "leaf2", "leaf3", "spine1", "spine2"],
                    "description": "First device to compare",
                },
                "device_b": {
                    "type": "string",
                    "enum": ["leaf1", "leaf2", "leaf3", "spine1", "spine2"],
                    "description": "Second device to compare",
                },
                "path": {
                    "type": "string",
                    "description": "gNMI path to compare (e.g. /network-instance[name=default]/protocols/bgp)",
                },
            },
            "required": ["device_a", "device_b", "path"],
        },
    },
},
```

The schema tells the LLM what parameters it can pass. `required` fields must always be provided; optional fields get defaults in your tool function.

## 3. Add dispatch logic in agent.py

In `src/srl_explorer/agent.py`, import your function and add a handler in `_execute_tool()`:

```python
# At the top, with other imports
from srl_explorer.tools.config_diff import config_diff

# In _execute_tool(), before the final raise ValueError
if name == "config_diff":
    return await config_diff(
        self.config,
        device_a=args["device_a"],
        device_b=args["device_b"],
        path=args["path"],
    )
```

The dispatch is a simple if-chain — match the tool name from the LLM's function call to your implementation.

## 4. Update the system prompt

In `src/srl_explorer/prompts.py`, add guidance to the `SYSTEM_PROMPT` so the LLM knows when to use your tool:

```
7. **Config comparison**:
   - When the user asks to compare config between devices → config_diff
   - For single-device config inspection → gnmic_get with data_type="CONFIG"
```

This is important — without guidance, the LLM might use gnmic_get twice and try to diff the results itself, which wastes tokens and is error-prone.

## 5. Test it

Start the REPL and try a query that should trigger your tool:

```
srl> compare the BGP config between leaf1 and leaf2
```

Watch the `>>>` output to confirm the LLM calls `config_diff` with the right arguments. Check the turn logs in `./logs/` to see the full request/response cycle.

You can also add a unit test in `tests/test_agent.py` following the existing patterns — mock the OpenAI response to include your tool call, mock the tool function itself, and verify the dispatch works correctly.
