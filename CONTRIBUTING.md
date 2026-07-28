# Contributing to hw-verify-mcp

## Adding a tool

1. Write it in `hwverify/tools.py` as a **plain function**: JSON-shaped in, JSON-shaped
   out, no MCP imports. Almost all behaviour belongs here, where it can be tested without
   a protocol handshake.
2. Register it in `TOOLS` in `hwverify/server.py` with a description and a JSON Schema.
3. Add it to `mcp-manifest.json`. A test fails if the manifest and the server disagree.

## Every refusal must carry a `next_step`

This is the rule that makes the server worth using from an agent rather than a CLI. A tool
that returns `LEAKY` and stops has told the agent nothing it can act on. Say which signal,
and say what to change.

Do not write a `next_step` that lets the agent conclude it has succeeded. The phrasing to
preserve is "call this tool again" — the checker sets the verdict, never the caller.

## Errors are return values

Raise `ToolError` for bad input; `dispatch` turns it into `{"error": ...}`. Do not let
exceptions escape to the transport: an agent recovers from a JSON error and cannot recover
from a dropped connection.

## Do not weaken the scope statements

Each backend's limits (glitch-free `d=1` masking; completion-timing-only constant-time;
modelled bit semantics for patch completeness) are surfaced in tool output on purpose. An
agent that does not know the boundary will assert past it, in a code review, in someone's
words. Keep `out_of_model`, `model`, and `modelled_leakage` in the responses.

## Keep `prove_confidential` listed

It is the one tool that always refuses. Listing it is deliberate: the boundary between what
is free and what is not should be discoverable, not hidden.

## Style

`ruff check .` clean, `pytest tests -q` green.
