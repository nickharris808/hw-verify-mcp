# Honest scope — what this server proves, and what it does not

This server exposes three checkers to an AI agent. Everything below about scope is
inherited from them; read their SCOPE documents for the detail:

- [`ctbench` SCOPE](https://github.com/nickharris808/ctbench/blob/main/SCOPE.md) — constant-time RTL
- [`ct-mask` SCOPE](https://github.com/nickharris808/ct-mask/blob/main/SCOPE.md) — first-order masking
- [`patchproof` SCOPE](https://github.com/nickharris808/patchproof/blob/main/SCOPE.md) — patch completeness

## The one thing specific to the agent setting

**The agent cannot mark its own homework.** An LLM writing constant-time RTL produces
something plausible, and plausible is the failure mode. Only the checker sets the
verdict; every refusal carries a `next_step` telling the agent what to do about it.

That makes the third verdict the important one:

- `check_constant_time` returns `UNKNOWN` for a design the analysis cannot read, with
  a `next_step` that says plainly the design has **not** been shown constant-time.
- `find_leak` returns `leaks: null` for `UNKNOWN`, not `false`. An agent writing
  `if not result["leaks"]` cannot treat an unanalysable design as clean.

An agent handed a result with no verdict will otherwise narrate it as success. That
is the specific hallucination this server exists to prevent, and it is why the
tri-state is a `null` rather than a convenient boolean.

## What the server does not do

- `prove_confidential` returns `{"available": false, ...}`. Proving a property to a
  third party who never receives the design is a commercial capability and is not
  implemented here. The tool exists so an agent asking for it gets a clear answer
  rather than an invented one.
- No tool here analyses anything you have not supplied in full.
