# CLAUDE.md

Project-specific instructions for Claude Code when working on srl-explorer.

## After every code change

1. Run `make lint` to check for issues
2. Run `make test` to verify nothing is broken
3. Fix any failures before moving on

Do not skip these steps. Do not batch them to the end.

## Use make targets

This project has a Makefile. Use it. `make lint`, `make test`, `make format`, `make run`. Do not call ruff, pytest, or uv directly when a make target exists.

## Package management

Use `uv`, never `pip`. Dependencies are managed in `pyproject.toml`. Do not add new dependencies without explicitly asking first.

## Project structure

- Source lives in `src/srl_explorer/` using the src layout
- Tests live in `tests/` and use pytest + pytest-asyncio
- `srlinux-yang-models/` is a cloned external repo. Never modify files in it.
- `srl-telemetry-lab/` is a cloned external repo like `srlinux-yang-models/`. Never modify files in it.
- `.cache/`, `logs/`, `.env` should Never be commited.

## Code style

- This is an async codebase. New tools must be async functions.
- Keep comments short. One or two lines max.
- Type hints on function signatures. Use `from __future__ import annotations`.
- This is a teaching codebase, prefer simplicy and readability over complexity
- Try to avoid code that might trigger pylance complaints

## Documentation
- docs/assorted_prompts are to never be modified, these are just a record of planning prompts
- ARCHITECTURE.md contains current information on behavior, when making changes always make sure it is updated appropriately
- README.md is primarily to help get the user up and running and to explain what the repo is for

## Test patterns

Tests mock the OpenAI API. Use the existing helpers in `tests/test_agent.py`:
- `_make_config()` and `_make_agent()` for setup
- `_mock_response()` for building fake LLM responses
- `_mock_tool_call()` for building fake tool calls (uses real `ChatCompletionMessageToolCall` types)
- `patch("srl_explorer.agent.<tool_name>")` for mocking tool functions

Do not create new test helper patterns when existing ones work.
