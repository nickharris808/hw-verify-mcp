"""MCP server exposing hardware-verification tools to AI agents.

A thin adapter over `tools.py`. The interesting design decision is in the tool
descriptions: each one tells the agent that it cannot declare success itself. The
agent writes RTL, the server refuses it and names the offending signals, the agent
fixes it and re-submits, the server confirms. Only the checker sets the verdict.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from . import tools

SERVER_NAME = "hw-verify"

#: name -> (handler, description, JSON-Schema input)
TOOLS: dict[str, tuple[Any, str, dict]] = {
    "check_constant_time": (
        tools.check_constant_time,
        (
            "Decide whether a Verilog module's completion signal is independent of "
            "declared secret inputs. Returns CONSTANT_TIME or LEAKY, and on LEAKY names "
            "the secrets that reach the completion signal. You cannot declare a design "
            "constant-time yourself: re-run this tool after any fix."
        ),
        {
            "type": "object",
            "properties": {
                "verilog": {"type": "string", "description": "Verilog-2001 source"},
                "observation": {
                    "type": "string",
                    "description": "completion signal name (default 'done')",
                },
                "secrets": {
                    "type": "array", "items": {"type": "string"},
                    "description": "input names carrying sensitive values; never inferred",
                },
                "module": {"type": "string", "description": "module name, if several"},
            },
            "required": ["verilog", "secrets"],
        },
    ),
    "find_leak": (
        tools.find_leak,
        (
            "Localise a timing leak: name the secret inputs that reach the completion "
            "signal, and how large its fan-in cone is."
        ),
        {
            "type": "object",
            "properties": {
                "verilog": {"type": "string"},
                "observation": {"type": "string"},
                "secrets": {"type": "array", "items": {"type": "string"}},
                "module": {"type": "string"},
            },
            "required": ["verilog", "secrets"],
        },
    ),
    "list_benchmark_fixtures": (
        tools.list_benchmark_fixtures,
        (
            "List the ctbench matched-pair corpus: constant-time designs each paired with "
            "a deliberately leaky twin of identical interface, plus an out-of-remit control."
        ),
        {"type": "object", "properties": {}},
    ),
    "get_benchmark_fixture": (
        tools.get_benchmark_fixture,
        "Fetch the Verilog source of one bundled benchmark fixture.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "e.g. cmp_leaky.v"}},
            "required": ["name"],
        },
    ),
    "score_benchmark_submission": (
        tools.score_benchmark_submission,
        (
            "Grade a set of verdicts against the benchmark. Reports unsound verdicts "
            "(said safe, is leaky) separately from imprecise ones, because only the first "
            "kind ships a vulnerability."
        ),
        {
            "type": "object",
            "properties": {
                "verdicts": {
                    "type": "object",
                    "description": "fixture file name -> CONSTANT_TIME | LEAKY | UNKNOWN",
                    "additionalProperties": {"type": "string"},
                }
            },
            "required": ["verdicts"],
        },
    ),
    "run_reference_checker": (
        tools.run_reference_checker,
        "Run the bundled cone-of-influence baseline over the whole benchmark corpus.",
        {"type": "object", "properties": {}},
    ),
    "check_masking": (
        tools.check_masking,
        (
            "Certify a masked gadget first-order secure under glitch-free probing, or "
            "name the probe wire that recombines a secret. Accepts a bundled gadget name "
            "or a JSON netlist. Two certificates are tried: dependence (touches at most "
            "one share) and uniformity (a fresh mask always flips the wire)."
        ),
        {
            "type": "object",
            "properties": {
                "gadget": {
                    "description": "bundled gadget name, or a netlist spec object",
                    "anyOf": [{"type": "string"}, {"type": "object"}],
                }
            },
            "required": ["gadget"],
        },
    ),
    "list_masking_gadgets": (
        tools.list_masking_gadgets,
        "List the bundled masking gadgets and the JSON netlist format for your own.",
        {"type": "object", "properties": {}},
    ),
    "check_patch_complete": (
        tools.check_patch_complete,
        (
            "Decide whether a modelled bounds-check repair eliminates EVERY violating "
            "input, not just a known one. Returns COMPLETE, INCOMPLETE (with a surviving "
            "violating input), or VACUOUS (the guard rejects everything)."
        ),
        {
            "type": "object",
            "properties": {
                "defect_class": {"type": "string", "description": "e.g. A, B, C"}
            },
            "required": ["defect_class"],
        },
    ),
    "list_defect_classes": (
        tools.list_defect_classes,
        (
            "List the modelled bounds-check defect classes, and what a COMPLETE verdict "
            "explicitly does not cover."
        ),
        {"type": "object", "properties": {}},
    ),
    "replay_certificate": (
        tools.replay_certificate,
        (
            "Re-check an elimination certificate using integer arithmetic only. No solver "
            "is used, so this verifies someone else's claim without trusting them or an "
            "SMT solver."
        ),
        {
            "type": "object",
            "properties": {"certificate": {"type": "object"}},
            "required": ["certificate"],
        },
    ),
    "prove_confidential": (
        tools.prove_confidential,
        (
            "Prove a property to a third party WITHOUT disclosing the design. Not "
            "available in the open-source distribution; call it to see what is."
        ),
        {"type": "object", "properties": {}},
    ),
}


def list_tool_specs() -> list[Tool]:
    return [
        Tool(name=name, description=desc, inputSchema=schema)
        for name, (_, desc, schema) in TOOLS.items()
    ]


def dispatch(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Call one tool by name. Errors come back as data, not exceptions.

    An agent handles `{"error": ...}` far better than a transport-level failure, and
    a refusal is a normal outcome here rather than a fault.
    """
    entry = TOOLS.get(name)
    if entry is None:
        return {"error": f"unknown tool {name!r}", "available": sorted(TOOLS)}
    handler = entry[0]
    try:
        return handler(**(arguments or {}))
    except tools.ToolError as exc:
        return {"error": str(exc), "tool": name}
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}", "tool": name}


def build_server() -> Server:
    server = Server(SERVER_NAME)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return list_tool_specs()

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        result = dispatch(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server


async def _amain() -> None:  # pragma: no cover - exercised by the stdio smoke test
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:  # pragma: no cover - entry point
    import asyncio

    asyncio.run(_amain())


if __name__ == "__main__":  # pragma: no cover
    main()
