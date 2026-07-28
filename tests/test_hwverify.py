"""Tests for hw-verify-mcp.

Two layers, deliberately:

* the tool functions, tested directly — most of the behaviour lives here and needs
  no protocol machinery;
* one real MCP session over the in-memory transport, so the wiring is exercised
  end-to-end rather than assumed.

The test that matters most is `test_agent_loop_refuse_fix_confirm`: it walks the
loop the server exists to support.
"""

from __future__ import annotations

import json

import pytest

from hwverify import tools
from hwverify.server import TOOLS, build_server, dispatch, list_tool_specs

LEAKY_RTL = """
module cmp_leaky (clk, rst, start, x, y, done, equal);
    parameter W = 8;
    input clk, rst, start;
    input [W-1:0] x, y;
    output done;
    output equal;
    reg [W-1:0] xr, yr;
    reg diff, running;
    assign done  = running & (xr == yr);
    assign equal = ~diff;
    always @(posedge clk) begin
        if (rst) begin
            xr <= 0; yr <= 0; diff <= 1'b0; running <= 1'b0;
        end else if (start) begin
            xr <= x; yr <= y; running <= 1'b1;
        end else if (running) begin
            if (xr != yr) begin
                diff <= 1'b1;
                running <= 1'b0;
            end
        end
    end
endmodule
"""

FIXED_RTL = """
module cmp_fixed (clk, rst, start, x, y, done, equal);
    parameter W = 8;
    input clk, rst, start;
    input [W-1:0] x, y;
    output done;
    output equal;
    reg [W-1:0] xr, yr;
    reg diff, running;
    reg [3:0] cnt;
    assign done  = running & (cnt == W);
    assign equal = ~diff;
    always @(posedge clk) begin
        if (rst) begin
            xr <= 0; yr <= 0; diff <= 1'b0; cnt <= 4'd0; running <= 1'b0;
        end else if (start) begin
            xr <= x; yr <= y; diff <= 1'b0; cnt <= 4'd0; running <= 1'b1;
        end else if (running) begin
            cnt <= cnt + 1'b1;
        end
    end
endmodule
"""


# ---------------------------------------------------------------------------
# The loop the server exists to support.
# ---------------------------------------------------------------------------

def test_agent_loop_refuse_fix_confirm():
    """Refuse a leaky design, guide the fix, confirm the fixed one. In that order."""
    bad = dispatch("check_constant_time",
                   {"verilog": LEAKY_RTL, "secrets": ["x", "y"]})
    assert bad["verdict"] == "LEAKY"
    assert set(bad["reaching_secrets"]) == {"x", "y"}
    assert "data-oblivious counter" in bad["next_step"], "refusal must say what to do"
    assert "does not count" in bad["next_step"], "agent must not self-certify"

    good = dispatch("check_constant_time",
                    {"verilog": FIXED_RTL, "secrets": ["x", "y"]})
    assert good["verdict"] == "CONSTANT_TIME"
    assert good["reaching_secrets"] == []


def test_masking_refusal_names_the_wire_and_the_repair():
    r = dispatch("check_masking", {"gadget": "naive_and"})
    assert r["verdict"] == "LEAKY"
    assert r["leaky_probes"] == ["c0", "c1"]
    assert "fresh mask" in r["next_step"]


def test_patch_refusal_hands_back_the_surviving_input():
    r = dispatch("check_patch_complete", {"defect_class": "A-badfix"})
    assert r["verdict"] == "INCOMPLETE"
    assert r["incompleteness_witness"] is not None
    assert "blocks a witness, not the class" in r["next_step"]


# ---------------------------------------------------------------------------
# Tool behaviour.
# ---------------------------------------------------------------------------

def test_secrets_are_never_inferred():
    r = dispatch("check_constant_time", {"verilog": LEAKY_RTL})
    assert "error" in r
    assert "never inferred" in r["error"]


def test_find_leak_reports_the_same_secrets():
    r = dispatch("find_leak", {"verilog": LEAKY_RTL, "secrets": ["x", "y"]})
    assert r["leaks"] is True
    assert set(r["reaching_secrets"]) == {"x", "y"}


def test_benchmark_fixture_listing_and_fetch():
    lst = dispatch("list_benchmark_fixtures", {})
    assert len(lst["scored"]) == 18
    assert any(e["role"] == "out_of_remit" for e in lst["scored"])
    src = dispatch("get_benchmark_fixture", {"name": "cmp_leaky.v"})
    assert "module cmp_leaky" in src["source"]
    assert src["manifest_entry"]["expected"] == "LEAKY"


def test_unknown_fixture_is_an_error_not_a_crash():
    r = dispatch("get_benchmark_fixture", {"name": "nope.v"})
    assert "error" in r and "no bundled fixture" in r["error"]


def test_reference_checker_scores_the_corpus():
    r = dispatch("run_reference_checker", {})
    assert r["score"]["correct"] == 18
    assert r["score"]["sound"] is True


def test_scoring_flags_unsoundness():
    verdicts = {e["file"]: e["expected"]
                for e in dispatch("list_benchmark_fixtures", {})["scored"]}
    verdicts["cmp_leaky.v"] = "CONSTANT_TIME"
    r = dispatch("score_benchmark_submission", {"verdicts": verdicts})
    assert r["sound"] is False
    assert r["unsound_verdicts"] == ["cmp_leaky.v"]
    assert "ships a vulnerability" in r["next_step"]


def test_masking_accepts_a_json_netlist():
    spec = {
        "name": "inline",
        "inputs": [
            {"name": "a0", "kind": "share", "of_secret": "a"},
            {"name": "a1", "kind": "share", "of_secret": "a"},
            {"name": "z", "kind": "mask"},
        ],
        "gates": [{"name": "t", "kind": "xor", "inputs": ["a0", "z"]}],
        "outputs": ["t"],
    }
    r = dispatch("check_masking", {"gadget": spec})
    assert r["verdict"] == "SECURE"


def test_malformed_netlist_is_a_clean_error():
    r = dispatch("check_masking", {"gadget": {"inputs": []}})
    assert "error" in r and "missing field" in r["error"]


def test_netlist_validation_errors_surface():
    spec = {"inputs": [{"name": "a0", "kind": "share"}], "gates": []}
    r = dispatch("check_masking", {"gadget": spec})
    assert "error" in r and "must name the secret" in r["error"]


def test_certificate_replay_round_trip():
    cert = dispatch("check_patch_complete", {"defect_class": "A"})["elimination_leg"]
    ok = dispatch("replay_certificate", {"certificate": cert})
    assert ok["verified"] is True
    cert["constraints"][0]["multiplier"] = 0
    bad = dispatch("replay_certificate", {"certificate": cert})
    assert bad["verified"] is False
    assert "do not accept" in bad["next_step"]


def test_defect_class_listing_separates_demos():
    r = dispatch("list_defect_classes", {})
    assert set(r["real_classes"]) == {"A", "B", "C"}
    demos = [c["key"] for c in r["classes"] if c["is_demo"]]
    assert "A-badfix" in demos and "A-vacuous" in demos
    assert r["out_of_model"], "scope limits must be exposed to the agent"


def test_unknown_defect_class_is_an_error():
    r = dispatch("check_patch_complete", {"defect_class": "Z"})
    assert "error" in r and "unknown defect class" in r["error"]


# ---------------------------------------------------------------------------
# The commercial boundary is visible, not hidden.
# ---------------------------------------------------------------------------

def test_confidential_proving_is_listed_but_refuses():
    assert "prove_confidential" in TOOLS
    r = dispatch("prove_confidential", {})
    assert r["available"] is False
    assert "commercial" in r["reason"]
    assert r["what_is_available_here"]


# ---------------------------------------------------------------------------
# Dispatch hygiene.
# ---------------------------------------------------------------------------

def test_unknown_tool_lists_the_real_ones():
    r = dispatch("nope", {})
    assert "error" in r and set(r["available"]) == set(TOOLS)


def test_bad_arguments_are_data_not_exceptions():
    r = dispatch("check_constant_time", {"wrong_kwarg": 1})
    assert "error" in r and "bad arguments" in r["error"]


def test_every_tool_has_a_schema_and_description():
    for name, (handler, desc, schema) in TOOLS.items():
        assert callable(handler), name
        assert len(desc) > 40, f"{name}: description too thin for an agent"
        assert schema["type"] == "object", name
        for req in schema.get("required", []):
            assert req in schema["properties"], f"{name}: required {req!r} not in properties"


def test_tool_specs_serialise():
    specs = list_tool_specs()
    assert len(specs) == len(TOOLS)
    assert all(s.name and s.description and s.inputSchema for s in specs)


def test_every_result_is_json_serialisable():
    for name in ("list_benchmark_fixtures", "list_masking_gadgets", "list_defect_classes",
                 "run_reference_checker", "prove_confidential"):
        json.dumps(dispatch(name, {}), default=str)


# ---------------------------------------------------------------------------
# One real MCP session, over the in-memory transport.
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_end_to_end_mcp_session():
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(build_server()) as client:
        listed = await client.list_tools()
        assert {t.name for t in listed.tools} == set(TOOLS)

        result = await client.call_tool(
            "check_constant_time", {"verilog": LEAKY_RTL, "secrets": ["x", "y"]}
        )
        payload = json.loads(result.content[0].text)
        assert payload["verdict"] == "LEAKY"
        assert set(payload["reaching_secrets"]) == {"x", "y"}

        moat = await client.call_tool("prove_confidential", {})
        assert json.loads(moat.content[0].text)["available"] is False


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_availability_reports_all_three_packages():
    assert tools.AVAILABILITY == {"ctbench": True, "ct-mask": True, "patchproof": True}


def test_manifest_matches_the_server():
    """The published manifest must not drift from the tools actually exposed."""
    import pathlib

    manifest = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent / "mcp-manifest.json").read_text()
    )
    assert set(manifest["tools"]) == set(TOOLS)
    assert manifest["server"]["command"] == "hw-verify-mcp"


def test_installed_entry_point_speaks_stdio_json_rpc():
    """Drive the real `hw-verify-mcp` process, not the in-memory transport.

    The in-memory session tests the handlers; this tests that the *shipped binary*
    starts, completes the handshake, and answers a tool call. stdin is held open
    throughout, which is how an MCP client behaves — closing it immediately makes
    the server shut down before later replies are written.
    """
    import queue
    import shutil
    import subprocess
    import threading

    exe = shutil.which("hw-verify-mcp")
    if exe is None:
        pytest.skip("hw-verify-mcp is not on PATH (package not installed)")

    proc = subprocess.Popen(
        [exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, bufsize=0,
    )
    inbox: queue.Queue[str] = queue.Queue()

    def pump() -> None:
        for line in proc.stdout:
            inbox.put(line.decode().strip())

    threading.Thread(target=pump, daemon=True).start()

    def send(msg: dict) -> None:
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        proc.stdin.flush()

    def reply(msg_id: int) -> dict:
        while True:
            line = inbox.get(timeout=60)
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("id") == msg_id:
                return msg

    try:
        send({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        })
        assert reply(1)["result"]["serverInfo"]["name"] == "hw-verify"

        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        assert len(reply(2)["result"]["tools"]) == len(TOOLS)

        send({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "check_masking", "arguments": {"gadget": "naive_and"}},
        })
        payload = json.loads(reply(3)["result"]["content"][0]["text"])
        assert payload["verdict"] == "LEAKY"
        assert payload["leaky_probes"] == ["c0", "c1"]
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=10)
