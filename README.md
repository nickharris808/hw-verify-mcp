# hw-verify-mcp

**Ask Claude "is this Verilog constant-time?" and get a formal answer with the leaking signals named — not a guess.**

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-stdio-purple.svg)](mcp-manifest.json)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![CI](https://img.shields.io/badge/CI-test%20matrix-brightgreen.svg)](.github/workflows/ci.yml)

## Why this exists

An LLM asked to write constant-time RTL will produce something plausible. Plausible is
exactly the failure mode: early-exit comparisons and data-dependent loop bounds *look*
fine. The model has no way to check, and neither does the person reading the diff.

This server gives the agent a checker it cannot argue with. The loop is:

1. the agent writes RTL;
2. the server **refuses** it and names the secrets that reach the completion signal;
3. the agent applies the suggested repair;
4. the server confirms — or refuses again.

**The agent cannot declare success.** Every refusal carries a `next_step` telling it what
to change, and every tool description says a self-asserted verdict does not count. That
constraint is the product: it makes the checker's refusal semantics the grammar the agent
learns to think in.

## Install

> **Not yet on PyPI.** Install from a checkout:

```bash
git clone https://github.com/nickharris808/hw-verify-mcp.git && cd hw-verify-mcp
pip install .
```

This pulls in `ctbench`, `ct-mask`, and `patchproof`, which do the actual analysis.

## 30-second quickstart

Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "hw-verify": {
      "command": "hw-verify-mcp"
    }
  }
}
```

Then ask: *"Write me a constant-time 8-bit tag comparator, and check it with hw-verify."*

Verify the server is wired up before you trust the agent's answers:

```console
$ hw-verify-mcp --version 2>/dev/null; python -c "
from hwverify.server import TOOLS
from hwverify.tools import AVAILABILITY
print(len(TOOLS), 'tools'); print(AVAILABILITY)"
12 tools
{'ctbench': True, 'ct-mask': True, 'patchproof': True}
```

If any backend reports `False`, the tools that need it return a clear error rather than a
wrong answer.

## Worked example — the loop, verbatim

The agent writes the obvious early-exit comparator and calls `check_constant_time`:

```json
{
  "verdict": "LEAKY",
  "observation": "done",
  "reaching_secrets": ["x", "y"],
  "cone_size": 9,
  "model": "Syntactic fan-in cone of the observation signal, including every enclosing if/case guard. Over-approximate: CONSTANT_TIME is conservative.",
  "next_step": "The completion signal depends on x, y. Make the completion condition a function of a data-oblivious counter rather than of operand values: run the loop a fixed number of cycles and drop any early-exit branch. Then call this tool again — a verdict you assert yourself does not count."
}
```

The agent replaces the early exit with a fixed counter and re-submits:

```json
{
  "verdict": "CONSTANT_TIME",
  "reaching_secrets": [],
  "next_step": "No secret reaches the completion signal. Note this covers completion timing only, not power, EM, or cache channels."
}
```

That exact sequence is a test (`test_agent_loop_refuse_fix_confirm`), so the loop is
verified rather than illustrated.

## The tools

| Tool | What it does |
|---|---|
| `check_constant_time` | CONSTANT_TIME or LEAKY for a Verilog module, with the reaching secrets named |
| `find_leak` | just the localisation: which secrets reach the completion signal, and the cone size |
| `list_benchmark_fixtures` | the ctbench matched-pair corpus, with expected verdicts |
| `get_benchmark_fixture` | the Verilog source of one fixture, so the agent can reason about it |
| `score_benchmark_submission` | grade a set of verdicts; unsound is reported separately from imprecise |
| `run_reference_checker` | run the bundled baseline over the whole corpus |
| `check_masking` | first-order masking verification of a gadget, by name or as a JSON netlist |
| `list_masking_gadgets` | the masking corpus, and the netlist format for your own |
| `check_patch_complete` | does a bounds-check repair eliminate *every* violating input? |
| `list_defect_classes` | the modelled defect classes, and what a COMPLETE verdict excludes |
| `replay_certificate` | re-check an elimination certificate using integer arithmetic, no solver |
| `prove_confidential` | **not available** — see below |

### Secrets are never inferred

`check_constant_time` refuses to guess which inputs are sensitive:

```json
{ "error": "no secrets declared. Secrets are a specification choice and are never inferred: pass the input names that carry sensitive values." }
```

Guessing here would be worse than useless — it would produce confident verdicts about the
wrong property.

## Errors are data, not faults

A refusal is a normal outcome. Unknown tools, bad arguments, and malformed netlists all
come back as `{"error": ...}` rather than as transport failures, because an agent recovers
from a JSON error and cannot recover from a broken connection.

## Honest scope

Everything the server inherits from its three backends, it also inherits the limits of:

- **Constant-time** verdicts cover *completion timing* against declared secrets — not
  power, EM, cache, or microarchitectural channels. The checker is a syntactic
  over-approximation, so `CONSTANT_TIME` is conservative and `LEAKY` may be pessimistic.
- **Masking** is glitch-free, first-order (`d=1`), 2-share probing. The report separates
  mean-invariance from whole-distribution invariance and says which was established.
- **Patch completeness** is reachability in modelled bit semantics — not an RCE claim —
  and `list_defect_classes` returns the shapes deliberately outside the model.

## `prove_confidential`

The tool is in the list, and calling it tells you why:

> Every tool in this server analyses a design you supply in full. Proving a property to a
> third party who never receives the design is a different problem: it needs the result
> bound to a commitment of a design that stays hidden. That capability is commercial and
> is not part of this package.

It is listed rather than omitted deliberately. An agent that discovers the boundary is
more useful than one that silently never learns it exists.

## Development

```bash
pip install -e . && pytest tests -q && ruff check .
```

25 tests: the tool functions directly, one real MCP session over the in-memory transport,
and one that drives the **installed `hw-verify-mcp` binary** over stdio JSON-RPC (skipped
if the package is not on `PATH`). A further test asserts `mcp-manifest.json` lists exactly
the tools the server exposes, so the manifest cannot drift.

Note for anyone writing their own client: keep stdin **open**. Closing it immediately after
writing makes the server shut down before later replies are flushed — that is correct
stdio behaviour, and it will look like a hang or a dropped response if you batch-write.

<!-- portfolio:start -->
## Part of the hw-verify toolkit

Six open tools and a dataset for proving security properties of hardware and bounds checks. They share one boundary: **everything open analyses a design you disclose in full.**

| Project | What it does |
|---|---|
| [`ctbench`](https://github.com/nickharris808/ctbench) | Matched-pair constant-time RTL benchmark + leaderboard |
| [`patchproof`](https://github.com/nickharris808/patchproof) | Prove a bounds-check fix eliminates *every* violating input |
| [`ct-mask`](https://github.com/nickharris808/ct-mask) | First-order masking verification by two certificates |
| **`hw-verify-mcp`** (you are here) | MCP server — all three checkers, for AI agents |
| [`ct-audit-action`](https://github.com/nickharris808/ct-audit-action) | GitHub Action — fail a PR on a leaky completion signal |
| [`hw-verify demo`](https://github.com/nickharris808/hw-verify-space) | Browser demo of all three checkers |
| [`hw-verify` dataset](https://huggingface.co/datasets/nickh007/hw-verify) | 49 records, 3 splits, byte-reproducible from these tools |

**The commercial boundary.** Proving a property to a third party who never receives the design — a verdict bound to a commitment of a design that stays hidden — is a different problem and a commercial one. It is not in any of these packages.
<!-- portfolio:end -->

## License

Apache-2.0. See [LICENSE](LICENSE). Contributing: [CONTRIBUTING.md](CONTRIBUTING.md).
