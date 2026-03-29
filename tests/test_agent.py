from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from srl_explorer.agent import Agent, MAX_AGENT_ITERATIONS, MAX_TOOL_RESULT_SIZE
from srl_explorer.config import Config
from srl_explorer.tools.yang import YangIndex


def _make_config() -> Config:
    return Config(openai_api_key="sk-test", prometheus_url="http://localhost:9090")


def _make_agent(**kwargs) -> Agent:
    agent = Agent(_make_config(), YangIndex([]), **kwargs)
    return agent


def _mock_response(content: str | None, finish_reason: str = "stop", tool_calls=None):
    """Build a mock ChatCompletion response."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls

    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = finish_reason

    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    usage.total_tokens = 15

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def _mock_tool_call(tool_id: str, name: str, arguments: dict):
    """Build a mock tool call object."""
    tc = MagicMock()
    tc.id = tool_id
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments)
    return tc


@pytest.mark.asyncio
async def test_simple_response():
    """Agent returns content when LLM responds with finish_reason='stop'."""
    agent = _make_agent()
    agent.client.chat.completions.create = AsyncMock(
        return_value=_mock_response("Hello, world!")
    )

    result = await agent.chat("hi")
    assert result == "Hello, world!"


@pytest.mark.asyncio
async def test_tool_dispatch():
    """Agent dispatches tool calls and passes correct arguments to gnmic_get."""
    tc = _mock_tool_call("call_1", "gnmic_get", {
        "target": "leaf1",
        "path": "/system/name",
    })
    tool_response = _mock_response(None, finish_reason="tool_calls", tool_calls=[tc])
    final_response = _mock_response("The hostname is leaf1.")

    agent = _make_agent()
    agent.client.chat.completions.create = AsyncMock(
        side_effect=[tool_response, final_response]
    )

    with patch("srl_explorer.agent.gnmic_get", new_callable=AsyncMock) as mock_gnmic:
        mock_gnmic.return_value = {"name": "leaf1"}
        result = await agent.chat("get hostname of leaf1")

    mock_gnmic.assert_called_once_with(
        agent.config,
        target="leaf1",
        path="/system/name",
        data_type="ALL",
    )
    assert result == "The hostname is leaf1."


@pytest.mark.asyncio
async def test_tool_result_truncation():
    """Large tool results are truncated before being added to message history."""
    tc = _mock_tool_call("call_1", "get_current_time", {})
    tool_response = _mock_response(None, finish_reason="tool_calls", tool_calls=[tc])
    final_response = _mock_response("Done.")

    agent = _make_agent()
    agent.client.chat.completions.create = AsyncMock(
        side_effect=[tool_response, final_response]
    )

    # Make get_current_time return a huge result
    huge_result = {"data": "x" * (MAX_TOOL_RESULT_SIZE + 1000)}
    with patch.object(agent, "_execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = huge_result
        await agent.chat("what time is it")

    # Find the tool message in history
    tool_msgs = [m for m in agent.messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["content"].endswith("... [truncated, result too large]")
    assert len(tool_msgs[0]["content"]) < len(json.dumps(huge_result))


@pytest.mark.asyncio
async def test_max_iterations_exceeded():
    """Agent raises RuntimeError after exceeding MAX_AGENT_ITERATIONS."""
    tc = _mock_tool_call("call_1", "get_current_time", {})
    # Always return a tool call, never finish
    looping_response = _mock_response(None, finish_reason="tool_calls", tool_calls=[tc])

    agent = _make_agent()
    agent.client.chat.completions.create = AsyncMock(return_value=looping_response)

    with patch.object(agent, "_execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = {"utc_iso": "2026-01-01T00:00:00Z", "epoch": 0}
        with pytest.raises(RuntimeError, match=str(MAX_AGENT_ITERATIONS)):
            await agent.chat("loop forever")


@pytest.mark.asyncio
async def test_reasoning_extraction():
    """Reasoning tags are extracted, callback fires, and tags are stripped from history."""
    reasoning_content = "<reasoning>I need to check BGP state</reasoning>"
    tc = _mock_tool_call("call_1", "get_current_time", {})
    first_response = _mock_response(
        reasoning_content, finish_reason="tool_calls", tool_calls=[tc]
    )
    final_response = _mock_response("BGP is up.")

    reasoning_captured = []
    agent = _make_agent(on_reasoning=lambda text: reasoning_captured.append(text))
    agent.client.chat.completions.create = AsyncMock(
        side_effect=[first_response, final_response]
    )

    with patch.object(agent, "_execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = {"utc_iso": "2026-01-01T00:00:00Z", "epoch": 0}
        result = await agent.chat("show bgp")

    # Callback fired with extracted reasoning
    assert reasoning_captured == ["I need to check BGP state"]

    # Reasoning tags stripped from assistant messages in history
    assistant_msgs = [m for m in agent.messages if m.get("role") == "assistant"]
    for msg in assistant_msgs:
        if msg["content"]:
            assert "<reasoning>" not in msg["content"]

    assert result == "BGP is up."
