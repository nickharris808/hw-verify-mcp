"""Tool implementations, as plain functions.

Deliberately free of any MCP machinery: everything here takes JSON-shaped input and
returns JSON-shaped output, so the behaviour can be tested without a protocol
handshake. `server.py` is a thin adapter over this module.

Every tool that can *refuse* returns a `next_step` field. That is the point of
exposing these to an agent rather than to a human: on refusal the agent is told
what to do about it, applies a fix, and re-submits — and it cannot declare success
itself, because only the checker sets the verdict.
"""

from __future__ import annotations

from typing import Any

# The three analysis packages are optional at import time so that a missing one
# degrades to a clear message rather than breaking the whole server.
try:
    from ctbench.cli import FIXTURES, run_reference
    from ctbench.cone import UNKNOWN as CT_UNKNOWN
    from ctbench.cone import check as ct_check
    from ctbench.score import format_report, load_manifest
    from ctbench.score import score as ct_score

    HAVE_CTBENCH = True
except ImportError:  # pragma: no cover - exercised only in a broken install
    HAVE_CTBENCH = False

try:
    from ctmask.analysis import analyse as mask_analyse
    from ctmask.gadgets import GADGETS
    from ctmask.gadgets import build as mask_build
    from ctmask.netlist import Netlist

    HAVE_CTMASK = True
except ImportError:  # pragma: no cover
    HAVE_CTMASK = False

try:
    from patchproof.linear import replay as pp_replay
    from patchproof.model import CLASSES, OUT_OF_MODEL, REAL_CLASSES
    from patchproof.prover import prove as pp_prove

    HAVE_PATCHPROOF = True
except ImportError:  # pragma: no cover
    HAVE_PATCHPROOF = False


class ToolError(Exception):
    """A tool was called with input it cannot act on."""


def _require(flag: bool, package: str) -> None:
    if not flag:
        raise ToolError(
            f"the {package!r} package is not installed; "
            f"install it to enable this tool"
        )


# ---------------------------------------------------------------------------
# Constant-time analysis (ctbench)
# ---------------------------------------------------------------------------

def check_constant_time(
    verilog: str, observation: str = "done", secrets: list[str] | None = None,
    module: str | None = None,
) -> dict[str, Any]:
    """Decide whether a completion signal is independent of the declared secrets."""
    _require(HAVE_CTBENCH, "ctbench")
    if not secrets:
        raise ToolError(
            "no secrets declared. Secrets are a specification choice and are never "
            "inferred: pass the input names that carry sensitive values."
        )
    v = ct_check(verilog, observation, secrets, module)
    out = v.to_dict()
    out["model"] = (
        "Syntactic fan-in cone of the observation signal, including every enclosing "
        "if/case guard. Over-approximate within the supported subset, so CONSTANT_TIME "
        "is conservative there; anything outside the subset returns UNKNOWN rather "
        "than a verdict."
    )
    if v.status == CT_UNKNOWN:
        # The agent-facing case that matters most: an LLM handed "no verdict" will
        # otherwise narrate it as success. Say plainly that nothing was established.
        out["next_step"] = (
            f"NO VERDICT was reached, so this design has NOT been shown to be "
            f"constant-time — do not report it as passing. {v.reason} "
            f"Rewrite the module as a single flat module of assign/always statements "
            f"and call this tool again, or analyse the submodule directly."
        )
    elif v.constant_time:
        out["next_step"] = (
            "No secret reaches the completion signal. Note this covers completion "
            "timing only, not power, EM, or cache channels."
        )
    else:
        out["next_step"] = (
            f"The completion signal depends on {', '.join(v.reaching)}. Make the "
            f"completion condition a function of a data-oblivious counter rather than "
            f"of operand values: run the loop a fixed number of cycles and drop any "
            f"early-exit branch. Then call this tool again — a verdict you assert "
            f"yourself does not count."
        )
    return out


def find_leak(
    verilog: str, observation: str = "done", secrets: list[str] | None = None,
    module: str | None = None,
) -> dict[str, Any]:
    """Name the secrets that reach the observation signal, or report none."""
    r = check_constant_time(verilog, observation, secrets, module)
    return {
        # A tri-state, deliberately: `leaks` is None when no verdict was reached, so
        # a caller writing `if not result["leaks"]` cannot silently treat an
        # unanalysable design as leak-free.
        "leaks": None if r["verdict"] == "UNKNOWN" else r["verdict"] == "LEAKY",
        "verdict": r["verdict"],
        "observation": r["observation"],
        "reaching_secrets": r["reaching_secrets"],
        "cone_size": r["cone_size"],
        "next_step": r["next_step"],
    }


def list_benchmark_fixtures() -> dict[str, Any]:
    """The bundled matched-pair corpus, with expected verdicts."""
    _require(HAVE_CTBENCH, "ctbench")
    man = load_manifest()
    return {
        "task": man["task"],
        "scored": [
            {
                "file": e["file"], "module": e["module"], "expected": e["expected"],
                "pair": e.get("pair"), "role": e.get("role"), "secrets": e["secrets"],
                "observation": e["observation"], "note": e.get("note", ""),
            }
            for e in man["scored"]
        ],
        "unscored": [{"file": e["file"], "reason": e["reason"]} for e in man["unscored"]],
        "note": (
            "Every safe design has a leaky twin with an identical interface. A tool "
            "earns a pair only by calling the safe one safe and the leaky one leaky."
        ),
    }


def get_benchmark_fixture(name: str) -> dict[str, Any]:
    """Fetch one fixture's source so an agent can reason about it directly."""
    _require(HAVE_CTBENCH, "ctbench")
    path = FIXTURES / name
    if not path.is_file():
        raise ToolError(f"no bundled fixture named {name!r}; call list_benchmark_fixtures")
    man = load_manifest()
    entry = next(
        (e for e in man["scored"] + man["unscored"] if e["file"] == name), None
    )
    return {"file": name, "source": path.read_text(), "manifest_entry": entry}


def score_benchmark_submission(verdicts: dict[str, str]) -> dict[str, Any]:
    """Grade a mapping of fixture name to verdict against the benchmark."""
    _require(HAVE_CTBENCH, "ctbench")
    s = ct_score(verdicts, load_manifest())
    out = s.to_dict()
    out["report"] = format_report(s)
    out["next_step"] = (
        "Unsound verdicts are the ones that matter: a leaky design reported safe. "
        "Imprecision costs engineering time; unsoundness ships a vulnerability."
        if not s.sound
        else "No unsound verdicts."
    )
    return out


def run_reference_checker() -> dict[str, Any]:
    """Run the bundled baseline over the whole corpus."""
    _require(HAVE_CTBENCH, "ctbench")
    man = load_manifest()
    verdicts = run_reference(man)
    return {"verdicts": verdicts, "score": ct_score(verdicts, man).to_dict()}


# ---------------------------------------------------------------------------
# Masking analysis (ct-mask)
# ---------------------------------------------------------------------------

def _netlist_from_spec(spec: dict[str, Any]) -> Netlist:
    """Build a Netlist from a JSON gadget description."""
    if not isinstance(spec, dict):
        raise ToolError("gadget spec must be an object")
    n = Netlist(spec.get("name", "gadget"))
    try:
        for i in spec["inputs"]:
            n.add_input(i["name"], i["kind"], i.get("of_secret"))
        for g in spec["gates"]:
            n.add_gate(g["name"], g["kind"], *g["inputs"])
    except KeyError as exc:
        raise ToolError(f"gadget spec missing field: {exc}") from exc
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    n.outputs = spec.get("outputs", [])
    return n


def check_masking(gadget: dict[str, Any] | str) -> dict[str, Any]:
    """Certify a masked gadget first-order secure, or name the recombining wire.

    Accepts either a bundled gadget name or a JSON netlist spec.
    """
    _require(HAVE_CTMASK, "ct-mask")
    n = mask_build(gadget) if isinstance(gadget, str) else _netlist_from_spec(gadget)
    r = mask_analyse(n)
    out = r.to_dict()
    if r.secure:
        out["next_step"] = (
            "Secure in the glitch-free first-order probing model. This is a statement "
            "about the first moment only; see modelled_leakage for what was and was "
            "not established."
        )
    else:
        out["next_step"] = (
            f"Probes {', '.join(r.leaky_probes)} recombine a secret: each touches more "
            f"than one share of some secret and no fresh mask always flips it. Insert a "
            f"fresh mask into the cross term (refresh it before combining domains), then "
            f"call this tool again."
        )
    return out


def list_masking_gadgets() -> dict[str, Any]:
    """The bundled masking corpus, secure gadgets and broken controls alike."""
    _require(HAVE_CTMASK, "ct-mask")
    return {
        "gadgets": [
            {
                "name": k,
                "expected": v[1],
                "description": (v[0].__doc__ or "").strip().splitlines()[0],
            }
            for k, v in sorted(GADGETS.items())
        ],
        "spec_format": {
            "name": "my_gadget",
            "inputs": [
                {"name": "a0", "kind": "share", "of_secret": "a"},
                {"name": "a1", "kind": "share", "of_secret": "a"},
                {"name": "z", "kind": "mask"},
            ],
            "gates": [{"name": "t", "kind": "xor", "inputs": ["a0", "z"]}],
            "outputs": ["t"],
        },
        "input_kinds": ["secret", "share", "mask", "public"],
        "gate_kinds": ["and", "or", "xor", "xnor", "not", "buf"],
    }


# ---------------------------------------------------------------------------
# Patch completeness (patchproof)
# ---------------------------------------------------------------------------

def check_patch_complete(defect_class: str) -> dict[str, Any]:
    """Decide whether a modelled repair eliminates every violating input."""
    _require(HAVE_PATCHPROOF, "patchproof")
    if defect_class not in CLASSES:
        raise ToolError(
            f"unknown defect class {defect_class!r}; have {sorted(CLASSES)}"
        )
    r = pp_prove(defect_class)
    out = r.to_dict()
    if r.verdict == "COMPLETE":
        out["next_step"] = (
            "Every violating input is eliminated, on both the bit-precise and the "
            "elimination leg. The certificate can be replayed without a solver via "
            "replay_certificate. Check out_of_model for what the verdict excludes."
        )
    elif r.verdict == "INCOMPLETE":
        w = r.incompleteness_witness
        out["next_step"] = (
            f"The corrected guard still admits {w.render() if w else 'a violating input'}. "
            f"The patch blocks a witness, not the class: strengthen the guard itself "
            f"rather than excluding specific values, then re-run."
        )
    else:
        out["next_step"] = (
            "The guard rejects every input, safe ones included. It is trivially "
            "complete and useless; weaken it until it admits the safe cases."
        )
    return out


def list_defect_classes() -> dict[str, Any]:
    """The modelled defect classes, including the deliberate failing demos."""
    _require(HAVE_PATCHPROOF, "patchproof")
    return {
        "real_classes": list(REAL_CLASSES),
        "classes": [
            {
                "key": k,
                "title": c.title,
                "fields": {f.name: f.width for f in c.fields},
                "total_width": c.total_width,
                "note": c.note,
                "is_demo": k not in REAL_CLASSES,
            }
            for k, c in sorted(CLASSES.items())
        ],
        "out_of_model": OUT_OF_MODEL,
    }


def replay_certificate(certificate: dict[str, Any]) -> dict[str, Any]:
    """Re-check an elimination certificate using integer arithmetic only.

    No solver is involved, so this verifies a third party's claim without trusting
    either them or an SMT solver.
    """
    _require(HAVE_PATCHPROOF, "patchproof")
    ok, msg = pp_replay(certificate)
    return {
        "verified": ok,
        "detail": msg,
        "method": "Farkas multipliers replayed by integer arithmetic; no solver used.",
        "next_step": (
            "The multipliers combine the constraints into a contradiction."
            if ok
            else "The certificate does not check out; do not accept the claim it makes."
        ),
    }


# ---------------------------------------------------------------------------
# The commercial boundary, deliberately visible in the tool list.
# ---------------------------------------------------------------------------

def prove_confidential(**_: Any) -> dict[str, Any]:
    """Prove a property to a third party WITHOUT disclosing the design.

    Not available in the open-source distribution.
    """
    return {
        "available": False,
        "reason": (
            "Every tool in this server analyses a design you supply in full. Proving "
            "a property to a third party who never receives the design is a different "
            "problem: it needs the result bound to a commitment of a design that stays "
            "hidden. That capability is commercial and is not part of this package."
        ),
        "what_is_available_here": [
            "check_constant_time", "find_leak", "check_masking", "check_patch_complete",
            "replay_certificate",
        ],
        "next_step": "Use the open tools on designs you control.",
    }


AVAILABILITY = {
    "ctbench": HAVE_CTBENCH,
    "ct-mask": HAVE_CTMASK,
    "patchproof": HAVE_PATCHPROOF,
}
