#!/usr/bin/env python3
"""Run pinned Jev deterministic functions on strings in a mandatory network sandbox.

This is an offline reference evaluator, not a Codex approval policy or live-model test.
The runner reads committed source with Git; it never checks out or imports the Jev package.
Only deterministic functions and the first two branches of decide() are evaluated.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile

REFERENCE = "eugeniughelbur/jev-engineering"
REVISION = "412adce16f9326de70b9bae469328c21e26ca498"
MAX_SOURCE = 1024 * 1024
MAX_COMMAND = 16 * 1024
MAX_CASES = 1000
FUNCTIONS = {"hard_rule", "_one", "fast_path"}
CONSTANTS = {"COMPOUND", "CHAIN_SPLIT", "HARMLESS_REDIRECT", "CD"}


@dataclass(frozen=True)
class Decision:
    verdict: str
    source: str
    reason: str


def validate_fixture(fixture: dict) -> None:
    if fixture.get("reference_repository") != REFERENCE or fixture.get("reference_commit") != REVISION:
        raise ValueError("fixture must use the reviewed Jev repository and full pinned revision")
    if fixture.get("requirements") != {
        "network": False, "model_calls": 0, "isolated_home": True, "pinned_default_policy": True
    }:
        raise ValueError("fixture must require no network, zero model calls and isolated default policy")
    cases = fixture.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError("fixture needs a bounded nonempty case list")
    identifiers = set()
    for case in cases:
        identifier = case.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ValueError("case identifiers must be unique, nonempty strings")
        identifiers.add(identifier)
        if case.get("function") not in {"hard_rule", "fast_path"}:
            raise ValueError("only deterministic function cases are supported")
        if not isinstance(case.get("command"), str) or len(case["command"]) > MAX_COMMAND:
            raise ValueError("case command must be a bounded string")
        if case.get("expected") not in {None, "allow", "deny"}:
            raise ValueError("unsupported expected verdict")


def committed_file(reference: Path, path: str) -> str:
    # Fixed Git subcommand/path, no shell and no execution of case.command.
    result = subprocess.run(
        ["git", "--no-replace-objects", "--no-optional-locks", "-C", str(reference), "show", f"{REVISION}:{path}"],
        capture_output=True, timeout=30, check=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if len(result.stdout) > MAX_SOURCE:
        raise ValueError("committed reference input exceeds 1 MiB")
    return result.stdout.decode("utf-8")


def deterministic_namespace(source: str, policy: dict) -> dict:
    """Compile the pinned deterministic AST without package imports/config/API side effects."""
    parsed = ast.parse(source)
    selected = []
    seen_functions = set()
    seen_constants = set()
    decide = None
    for node in parsed.body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            if node.name in seen_functions:
                raise ValueError("duplicate reference function")
            seen_functions.add(node.name)
            selected.append(node)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in CONSTANTS:
            if node.targets[0].id in seen_constants:
                raise ValueError("duplicate reference constant")
            seen_constants.add(node.targets[0].id)
            if not (isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                    and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == "re"
                    and node.value.func.attr == "compile" and len(node.value.args) == 1
                    and isinstance(node.value.args[0], ast.Constant) and isinstance(node.value.args[0].value, str)
                    and not node.value.keywords):
                raise ValueError("unexpected reference regex constant")
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "decide":
            decide = node
    if seen_functions != FUNCTIONS or seen_constants != CONSTANTS or decide is None:
        raise ValueError("reference deterministic source shape changed")
    # Validate the actual upstream order, then include only these two branches.
    for node, name in zip(decide.body[:2], ("hard_rule", "fast_path")):
        if not (isinstance(node, ast.If) and not node.orelse
                and isinstance(node.test, ast.NamedExpr) and isinstance(node.test.target, ast.Name)
                and node.test.target.id == "found" and isinstance(node.test.value, ast.Call)
                and isinstance(node.test.value.func, ast.Name) and node.test.value.func.id == name
                and len(node.test.value.args) == 1 and isinstance(node.test.value.args[0], ast.Name)
                and node.test.value.args[0].id == "command" and not node.test.value.keywords
                and len(node.body) == 1 and isinstance(node.body[0], ast.Return)
                and isinstance(node.body[0].value, ast.Name) and node.body[0].value.id == "found"):
            raise ValueError("reference must check hard rules before the fast path")
    if len(decide.body) < 2:
        raise ValueError("missing deterministic decision branches")
    decide.body = decide.body[:2] + [ast.Return(value=ast.Constant(value=None))]
    decide.name = "decide_prefix"
    selected.append(decide)
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    # Restricted globals are a second guard; the OS sandbox is mandatory.
    for node in ast.walk(module):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.With, ast.AsyncWith, ast.Global, ast.Nonlocal)):
            raise ValueError("side-effecting source shape is outside the deterministic subset")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("dunder access is outside the deterministic subset")
    rules = policy.get("hard_deny")
    fast = policy.get("fast_path")
    if not isinstance(rules, list) or not isinstance(fast, list):
        raise ValueError("invalid pinned default policy")
    namespace = {
        "__builtins__": {"any": any, "all": all, "str": str, "bool": bool},
        "re": re, "Decision": Decision,
        "HARD_DENY": [(rule["pattern"], rule["reason"]) for rule in rules],
        "FAST_ALLOW": fast,
    }
    exec(compile(module, f"{REVISION}/jev_gate.py:deterministic", "exec"), namespace)
    return namespace


def evaluate(payload: dict) -> dict:
    fixture = payload["fixture"]
    validate_fixture(fixture)
    namespace = deterministic_namespace(payload["source"], payload["policy"])
    results = []
    for case in fixture["cases"]:
        decision = namespace[case["function"]](case["command"])
        verdict = None if decision is None else decision.verdict
        result = {"id": case["id"], "expected": case["expected"], "actual": verdict, "pass": verdict == case["expected"]}
        if case.get("also_verify"):
            # Only the validated deterministic prefix of upstream decide is available.
            ordered = namespace["decide_prefix"](case["command"])
            result["ordered_source"] = None if ordered is None else ordered.source
            result["pass"] = result["pass"] and ordered is not None and ordered.source == "hard-rule"
        results.append(result)
    return {
        "reference_repository": REFERENCE, "reference_revision": REVISION,
        "source_sha256": hashlib.sha256(payload["source"].encode()).hexdigest(),
        "scope": "pinned_deterministic_ast_only", "case_count": len(results),
        "passed": sum(item["pass"] for item in results), "model_calls": 0,
        "command_strings_executed": 0, "cases": results,
    }


def worker(path: Path) -> int:
    payload = json.loads(path.read_text())
    changed = os.readlink("/proc/self/ns/net") != payload["outer_netns"]
    interfaces = [name for _, name in socket.if_nameindex()]
    if not changed or any(name != "lo" for name in interfaces):
        raise RuntimeError("worker requires a distinct loopback-only network namespace")
    if any(os.environ.get(name) for name in ("OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "OPENAI_API_KEY")):
        raise RuntimeError("worker must not receive provider credentials")
    home = Path.home()
    if list(home.iterdir()):
        raise RuntimeError("worker HOME must start empty")
    result = evaluate(payload)
    result.update(network_isolated=True, network_namespace_changed=True, interfaces=interfaces, isolated_home=True)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] == result["case_count"] else 1


def sandbox_command(bwrap: str, payload: Path) -> list[str]:
    args = [bwrap, "--unshare-all", "--die-with-parent", "--new-session"]
    mounted = []
    for directory in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(directory).exists():
            args += ["--ro-bind", directory, directory]
            mounted.append(Path(directory))
    prefix = Path(sys.base_prefix).resolve()
    if not any(prefix == directory or directory in prefix.parents for directory in mounted):
        args += ["--ro-bind", str(prefix), str(prefix)]
    interpreter = Path(sys.executable).resolve()
    if not any(interpreter == directory or directory in interpreter.parents for directory in mounted + [prefix]):
        args += ["--ro-bind", str(interpreter), str(interpreter)]
    args += [
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/home/sandbox",
        "--ro-bind", str(payload), "/input.json",
        "--ro-bind", str(Path(__file__).resolve()), "/runner.py",
        "--clearenv", "--setenv", "HOME", "/home/sandbox",
        "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LC_ALL", "C.UTF-8",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--chdir", "/home/sandbox",
        str(interpreter), "-I", "/runner.py", "--worker", "/input.json",
    ]
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, help="Local Git checkout containing the pinned Jev commit")
    parser.add_argument("--fixture", type=Path, default=Path(__file__).parent / "fixtures/jev-deterministic-reference.json")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker)
    if args.reference is None:
        parser.error("--reference is required; fetch the pinned checkout separately")
    fixture = json.loads(args.fixture.read_text())
    validate_fixture(fixture)
    bwrap = shutil.which("bwrap")
    if not bwrap or sys.platform != "linux":
        raise RuntimeError("Linux Bubblewrap is required; unisolated fallback is prohibited")
    payload = {
        "fixture": fixture, "source": committed_file(args.reference, "jev_gate.py"),
        "policy": json.loads(committed_file(args.reference, "policy.json")),
        "outer_netns": os.readlink("/proc/self/ns/net"),
    }
    with tempfile.TemporaryDirectory(prefix="harness-jev-") as directory:
        path = Path(directory) / "input.json"
        path.write_text(json.dumps(payload))
        path.chmod(0o600)
        process = subprocess.run(sandbox_command(bwrap, path), capture_output=True, text=True, timeout=60, env={})
    if process.returncode not in (0, 1) or not process.stdout.strip():
        raise RuntimeError("network sandbox failed; no cases passed: " + process.stderr[-2000:])
    try:
        result = json.loads(process.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("worker did not return a valid evaluation report") from error
    if not result.get("network_isolated") or result.get("model_calls") != 0:
        raise RuntimeError("worker report is missing isolation/no-model evidence")
    print(json.dumps(result, indent=2, sort_keys=True))
    return process.returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Jev reference evaluation failed: {error}", file=sys.stderr)
        raise SystemExit(2)
