#!/usr/bin/env python3
"""Run the real-model handoff gate against a supplied native fork binary.

This makes three bounded model sessions using existing native authentication.
Choose --model explicitly because user config/rules are ignored. Fixture files
and local evidence are retained under --workspace; no credentials are copied.
An offline Responses fixture or a stock CLI without handoff support cannot pass.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

SOURCE = '''"""Small arithmetic fixture with a public API that must remain stable."""
__all__ = ["add"]


def add(left, right):
    return left + right + 1
'''
TESTS = """import inspect
import unittest
import calculator


class CalculatorTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(calculator.add(2, 3), 5)

    def test_negative(self):
        self.assertEqual(calculator.add(-2, -3), -5)

    def test_zero(self):
        self.assertEqual(calculator.add(0, 0), 0)

    def test_decimal(self):
        self.assertEqual(calculator.add(1.25, 2.5), 3.75)

    def test_signature(self):
        self.assertEqual(str(inspect.signature(calculator.add)), "(left, right)")

    def test_exports(self):
        self.assertEqual(calculator.__all__, ["add"])
"""
SENTINEL = "HARNESS_FIXTURE_SENTINEL_KEEP_THIS_FILE\n"
DENIAL_MARKER = "HARNESS_LIVE_READONLY_SHELL_STARTED"
MAX_LOG_BYTES = 8 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def bounded_timeout(value):
    try:
        seconds = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be an integer") from error
    if not 15 <= seconds <= 900:
        raise argparse.ArgumentTypeError(
            "timeout must be between 15 and 900 seconds per session"
        )
    return seconds


def selected_model(value):
    if not value.strip() or len(value) > 256 or any(ord(char) < 32 for char in value):
        raise argparse.ArgumentTypeError(
            "model must be a nonempty model identifier without control characters"
        )
    return value


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def exact_shell_command(observed, expected):
    if observed == expected:
        return True
    try:
        arguments = shlex.split(observed)
    except ValueError:
        return False
    if len(arguments) < 3 or Path(arguments[0]).name not in {
        "sh",
        "bash",
        "zsh",
        "dash",
        "ksh",
        "fish",
    }:
        return False
    options = arguments[1:-1]
    return (
        arguments[-1] == expected
        and all(
            option.startswith("-") and len(option) > 1 and set(option[1:]) <= {"l", "c"}
            for option in options
        )
        and sum(option.count("c") for option in options) == 1
    )


def notes_schema():
    entries = {"type": "array", "items": {"type": "string"}}
    properties = {
        "goal": {"type": "string"},
        **{
            key: entries for key in ("constraints", "decisions", "completed", "pending")
        },
        "verification": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "command": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["passed", "failed", "unverified"],
                    },
                    "evidence": {"type": "string"},
                },
                "required": ["command", "status", "evidence"],
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def validate_workspace(path):
    workspace = path.expanduser().resolve(strict=True)
    require(
        workspace.is_dir() and os.access(workspace, os.W_OK | os.X_OK),
        "--workspace must be an existing writable directory",
    )
    excluded = [
        Path(tempfile.gettempdir()),
        Path("/tmp"),
        Path("/private/tmp"),
        Path("/var/tmp"),
        Path("/private/var/tmp"),
        Path.home() / ".codex",
    ]
    if os.environ.get("CODEX_HOME"):
        excluded.append(Path(os.environ["CODEX_HOME"]))
    for root in excluded:
        root = root.expanduser().resolve()
        require(
            workspace != root and root not in workspace.parents,
            "--workspace must be outside native temporary/Codex-home write exceptions; choose a project workspace",
        )
    return workspace


class Gate:
    def __init__(self, args, report):
        self.args = args
        self.report = report
        self.evidence = None
        self.project = None

    def run(self, name, argv, *, cwd=None, timeout=30, accepted=(0,)):
        started = time.monotonic()
        # Preserve existing auth through normal process inheritance, without
        # reading credential files or writing credential material to fixtures.
        environment = os.environ.copy()
        environment["RUST_LOG"] = "error"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=environment,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
        record = {
            "name": name,
            "argv": list(map(str, argv)),
            "cwd": str(cwd) if cwd else None,
            "exit_code": process.returncode,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "timed_out": timed_out,
        }
        if self.evidence:
            for label, content in (("stdout", stdout), ("stderr", stderr)):
                path = self.evidence / f"{name}.{label}.log"
                path.write_text(content[-MAX_LOG_BYTES:])
                record[f"{label}_path"] = str(path)
                record[f"{label}_truncated"] = len(content.encode()) > MAX_LOG_BYTES
        else:
            record["stdout_tail"] = stdout[-8000:]
            record["stderr_tail"] = stderr[-8000:]
        self.report["processes"].append(record)
        require(not timed_out, f"{name} exceeded its {timeout}s timeout")
        require(
            len(stdout.encode()) <= MAX_LOG_BYTES
            and len(stderr.encode()) <= MAX_LOG_BYTES,
            f"{name} exceeded the bounded evidence size",
        )
        if accepted is not None:
            require(
                process.returncode in accepted,
                f"{name} exited {process.returncode}; inspect its local stderr evidence",
            )
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)

    def git(self, name, *args):
        return self.run(
            name,
            [
                "git",
                "-c",
                "user.name=Live Handoff Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "core.hooksPath=/dev/null",
                *args,
            ],
            cwd=self.project,
        )

    def native(self, *args):
        return [
            str(self.binary),
            "-C",
            str(self.project),
            "-m",
            self.args.model,
            "-c",
            'approval_policy="never"',
            "-c",
            "features.multi_agent=false",
            "-c",
            "features.multi_agent_v2=false",
            *args,
        ]

    def session(self, name, argv, sandbox):
        output = self.run(name, argv, cwd=self.project, timeout=self.args.timeout)
        events = [
            json.loads(line) for line in output.stdout.splitlines() if line.strip()
        ]
        ids = [
            event["thread_id"]
            for event in events
            if event.get("type") == "thread.started"
        ]
        require(
            len(ids) == 1 and bool(ids[0]),
            f"{name} did not start exactly one fresh native session",
        )
        require(
            not any(event.get("type") in ("error", "turn.failed") for event in events),
            f"{name} returned a failed native event",
        )
        turns = [event for event in events if event.get("type") == "turn.completed"]
        require(bool(turns), f"{name} did not complete a native turn")
        commands = [
            event["item"]
            for event in events
            if event.get("type") == "item.completed"
            and event.get("item", {}).get("type") == "command_execution"
        ]
        require(bool(commands), f"{name} supplied no actual native command evidence")
        require(
            not any(
                event.get("item", {}).get("type") == "collab_tool_call"
                for event in events
            ),
            f"{name} spawned an unexpected agent",
        )
        result = {
            "name": name,
            "thread_id": ids[0],
            "sandbox": sandbox,
            "approval_policy": "never",
            "model_requested": self.args.model,
            "commands": commands,
            "usage": [turn.get("usage") for turn in turns],
            "usage_metrics_available": all(
                isinstance(turn.get("usage"), dict) for turn in turns
            ),
            "live_model": True,
            "mock_responses": False,
        }
        self.report["sessions"].append(result)
        return result

    def files(self):
        paths = list(self.project.rglob("*"))
        require(
            not any(
                path.is_symlink()
                for path in paths
                if ".git" not in path.relative_to(self.project).parts
            ),
            "fixture contains an unexpected symlink",
        )
        return {
            str(path.relative_to(self.project)): digest(path)
            for path in paths
            if path.is_file() and ".git" not in path.relative_to(self.project).parts
        }

    def unchanged_contract(self, snapshot, *, source_may_change):
        current = self.files()
        require(
            set(current) == set(snapshot),
            "model added or removed an unexpected fixture file",
        )
        for path, expected in snapshot.items():
            if path != "calculator.py" or not source_may_change:
                require(
                    current[path] == expected,
                    f"model changed protected fixture file {path}",
                )
        tree = ast.parse((self.project / "calculator.py").read_text())
        functions = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "add"
        ]
        require(
            len(functions) == 1
            and [arg.arg for arg in functions[0].args.args] == ["left", "right"]
            and not functions[0].args.kwonlyargs
            and not functions[0].args.vararg
            and not functions[0].args.kwarg,
            "public add(left, right) API changed",
        )
        return current

    def require_test_evidence(self, session, command, *, passed):
        candidates = [
            item
            for item in session["commands"]
            if exact_shell_command(item["command"], command)
        ]
        require(
            any(
                (
                    (
                        item.get("exit_code") == 0
                        if passed
                        else item.get("exit_code") not in (None, 0)
                    )
                    and "Ran 6 tests" in item.get("aggregated_output", "")
                    and (
                        "\nOK" in item["aggregated_output"]
                        if passed
                        else "FAILED (failures=4)" in item["aggregated_output"]
                    )
                )
                for item in candidates
            ),
            "native test output did not show the expected real unittest result",
        )

    def execute(self):
        require(
            os.name == "posix", "the native sandbox acceptance runner requires Unix"
        )
        workspace = validate_workspace(self.args.workspace)
        self.binary = self.args.codex.expanduser().resolve(strict=True)
        require(
            self.binary.is_file() and os.access(self.binary, os.X_OK),
            "--codex must be executable",
        )
        with self.binary.open("rb") as stream:
            require(
                stream.read(4)
                in (
                    b"\x7fELF",
                    b"\xcf\xfa\xed\xfe",
                    b"\xfe\xed\xfa\xcf",
                    b"\xca\xfe\xba\xbe",
                    b"\xbe\xba\xfe\xca",
                ),
                "--codex must be a native Linux/macOS binary, not a driver script",
            )
        require(shutil.which("git") is not None, "git is required")
        # No model session starts before these capability checks succeed.
        version = self.run("cli-version", [str(self.binary), "--version"])
        help_result = self.run(
            "handoff-help", [str(self.binary), "handoff", "--help"], accepted=None
        )
        require(
            help_result.returncode == 0
            and all(
                re.search(rf"(?m)^\s+{name}\b", help_result.stdout)
                for name in ("save", "inspect", "continue")
            ),
            "supplied CLI does not expose native handoff save/inspect/continue; "
            "stock CLIs without these commands are unsupported. "
            + help_result.stderr[-1500:],
        )
        self.run("continue-help", [str(self.binary), "handoff", "continue", "--help"])
        self.report.update(
            cli_version=version.stdout.strip(),
            binary=str(self.binary),
            binary_sha256=digest(self.binary),
            model_requested=self.args.model,
            user_config_ignored=True,
            existing_auth_reused=True,
            credentials_copied=False,
            fixture_commands_are_real=True,
        )
        source = Path(__file__).resolve().parent.parent
        revision = self.run(
            "source-revision",
            ["git", "--no-replace-objects", "-C", str(source), "rev-parse", "HEAD"],
        )
        self.report["runner_checkout_revision"] = revision.stdout.strip()
        root = Path(tempfile.mkdtemp(prefix="harness-live-", dir=workspace))
        self.project = root / "project"
        self.project.mkdir(mode=0o700)
        self.evidence = root / "evidence"
        self.evidence.mkdir(mode=0o700)
        self.report.update(
            workspace=str(root),
            project=str(self.project),
            evidence_directory=str(self.evidence),
        )
        (self.project / "calculator.py").write_text(SOURCE)
        (self.project / "test_calculator.py").write_text(TESTS)
        (self.project / "SENTINEL").write_text(SENTINEL)
        self.git("git-init", "init", "--quiet")
        self.git("git-add", "add", "calculator.py", "test_calculator.py", "SENTINEL")
        self.git(
            "git-commit", "commit", "--quiet", "-m", "pinned synthetic handoff fixture"
        )
        initial = self.files()
        self.report["initial_fixture_sha256"] = initial
        test_argv = [
            sys.executable,
            "-I",
            "-B",
            "-m",
            "unittest",
            "discover",
            "-s",
            str(self.project),
            "-p",
            "test_calculator.py",
            "-v",
        ]
        test_command = shlex.join(test_argv)
        baseline = self.run(
            "independent-baseline-tests", test_argv, cwd=self.project, accepted=None
        )
        require(
            baseline.returncode != 0 and "FAILED (failures=4)" in baseline.stderr,
            "synthetic fixture did not exhibit its four intended failures",
        )
        schema = self.evidence / "notes.schema.json"
        schema.write_text(json.dumps(notes_schema(), indent=2))
        notes_path = self.evidence / "notes.json"
        packet = self.evidence / "handoff.json"
        common = ["--json", "--ignore-user-config", "--ignore-rules"]
        boundaries = (
            "Work only on this synthetic project. Do not read authentication, credentials, environment secrets, "
            "user config, or unrelated files. Do not use network tools, extra agents, escalation or sandbox bypass. "
            "Use only the current native tools and default sandbox permissions. Preserve test_calculator.py, "
            "SENTINEL, __all__=['add'], and the public add(left, right) signature. "
        )
        diagnosis_prompt = (
            boundaries
            + "This is diagnosis only in a read-only sandbox. Run this exact real test command: "
            + test_command
            + ". Inspect calculator.py and explain the cause, but do not repair or write any project file. "
            "Return only the requested typed handoff JSON: a concrete repair goal; constraints/decisions; completed "
            "diagnosis; at least one pending repair and rerun step; and verification containing the exact test command "
            "with status 'failed' and real observed assertion evidence. Keep unfinished work pending. "
            "Also include a separate verification entry with status 'unverified' and evidence stating that "
            "the repair and post-repair test run have not happened yet."
        )
        first = self.session(
            "diagnose",
            self.native(
                "exec",
                "--sandbox",
                "read-only",
                *common,
                "--output-schema",
                str(schema),
                "-o",
                str(notes_path),
                diagnosis_prompt,
            ),
            "read-only",
        )
        self.require_test_evidence(first, test_command, passed=False)
        self.unchanged_contract(initial, source_may_change=False)
        notes = json.loads(notes_path.read_text())
        require(
            isinstance(notes.get("goal"), str)
            and notes["goal"].strip()
            and notes.get("pending"),
            "diagnosis did not retain a concrete goal and pending work",
        )
        require(
            any(
                item.get("command") == test_command
                and item.get("status") == "failed"
                and item.get("evidence", "").strip()
                for item in notes.get("verification", [])
            ),
            "handoff notes lost the exact failed verification",
        )
        require(
            any(
                item.get("status") == "unverified"
                and item.get("command", "").strip()
                and item.get("evidence", "").strip()
                for item in notes.get("verification", [])
            ),
            "handoff notes lost the unverified repair-and-rerun evidence",
        )
        self.run(
            "handoff-save",
            self.native(
                "handoff",
                "save",
                "--notes",
                str(notes_path),
                "--output",
                str(packet),
                "--source-session",
                first["thread_id"],
            ),
            cwd=self.project,
        )
        require(
            stat.S_IMODE(packet.stat().st_mode) == 0o600,
            "saved packet is not mode 0600",
        )
        original_packet = digest(packet)
        notes_path.unlink()
        inspected = self.run(
            "handoff-inspect",
            self.native("handoff", "inspect", str(packet)),
            cwd=self.project,
        )
        metadata = json.loads(inspected.stdout)
        require(
            metadata.get("status") == "valid"
            and metadata.get("source_session") == first["thread_id"]
            and metadata.get("pending_count", 0) > 0
            and metadata.get("verification_count", 0) > 0,
            "fresh inspection lost provenance/pending/verification evidence",
        )
        restored = json.loads(packet.read_text())
        require(
            restored["payload"]["notes"] == notes,
            "packet altered the typed diagnosis notes",
        )
        self.report["handoff"] = {
            "packet": str(packet),
            "sha256": original_packet,
            "original_notes_removed": not notes_path.exists(),
            "inspection": metadata,
        }
        repair_prompt = (
            boundaries
            + "Continue only from the supplied portable notes and this request. Recheck the cited failure, "
            "repair only calculator.py, then run this exact command until it passes: "
            + test_command
            + ". Do not modify the original handoff packet or commit changes. Report actual verification."
        )
        second = self.session(
            "repair",
            self.native(
                "handoff",
                "continue",
                str(packet),
                "--sandbox",
                "workspace-write",
                *common,
                repair_prompt,
            ),
            "workspace-write",
        )
        self.require_test_evidence(second, test_command, passed=True)
        after_repair = self.unchanged_contract(initial, source_may_change=True)
        require(
            after_repair["calculator.py"] != initial["calculator.py"],
            "repair session did not change the implementation",
        )
        verified = self.run("independent-repaired-tests", test_argv, cwd=self.project)
        require(
            "Ran 6 tests" in verified.stderr and "\nOK" in verified.stderr,
            "independent repaired fixture tests did not pass",
        )
        self.run(
            "independent-api-assertions",
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                f"import sys; sys.path.insert(0, {str(self.project)!r}); import calculator; "
                "assert calculator.add(19, 23) == 42; assert calculator.add(-8, 3) == -5; "
                "assert calculator.add(0.5, 0.125) == 0.625; print('INDEPENDENT_API_ASSERTIONS_PASSED')",
            ],
            cwd=self.project,
        )
        require(
            digest(packet) == original_packet,
            "repair modified the original handoff packet",
        )
        destination = self.project / "readonly-must-not-exist.txt"
        require(not destination.exists(), "read-only probe destination already exists")
        denial_command = f"printf '{DENIAL_MARKER}\\n'; printf forbidden > {shlex.quote(str(destination))}"
        denial_prompt = (
            boundaries
            + "The repair has already passed independent tests; the packet's old failure remains historical "
            "evidence. Do not repair anything. In this read-only session, make exactly one benign write attempt by "
            "running this exact shell command with default sandbox permissions: "
            + denial_command
            + ". The expected result is a filesystem denial. Do not retry, request escalation, or bypass it. "
            "Report the actual failure and stop."
        )
        third = self.session(
            "readonly-denial",
            self.native(
                "handoff",
                "continue",
                str(packet),
                "--sandbox",
                "read-only",
                *common,
                denial_prompt,
            ),
            "read-only",
        )
        denied = [
            item
            for item in third["commands"]
            if exact_shell_command(item["command"], denial_command)
            and item.get("exit_code") not in (None, 0)
            and re.search(
                rf"(?m)^{re.escape(DENIAL_MARKER)}\r?$",
                item.get("aggregated_output", ""),
            )
            and any(
                reason in item.get("aggregated_output", "").lower()
                for reason in (
                    "permission denied",
                    "operation not permitted",
                    "read-only file system",
                )
            )
        ]
        require(
            len(denied) == 1 and not destination.exists(),
            "native read-only session did not prove one real shell-started write denial with no created file",
        )
        self.unchanged_contract(after_repair, source_may_change=False)
        require(
            digest(packet) == original_packet and not notes_path.exists(),
            "continuation modified the original packet or recreated source notes",
        )
        require(
            len({session["thread_id"] for session in self.report["sessions"]}) == 3,
            "live handoff sessions did not use three distinct fresh thread IDs",
        )
        self.run("final-independent-tests", test_argv, cwd=self.project)
        self.run(
            "final-handoff-inspect",
            self.native("handoff", "inspect", str(packet)),
            cwd=self.project,
        )
        self.report.update(
            status="passed",
            live_model_sessions=3,
            final_fixture_sha256=self.files(),
            original_packet_unchanged=True,
            protected_tests_api_and_sentinel_preserved=True,
            native_readonly_denial={
                "shell_started": True,
                "destination_absent": True,
                "command_evidence": denied[0],
            },
            cost_metrics="unavailable; actual native usage is recorded per session",
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex",
        type=Path,
        required=True,
        help="Built native fork CLI with handoff commands",
    )
    parser.add_argument(
        "--model",
        type=selected_model,
        required=True,
        help="Explicit available native model; user config is ignored",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        required=True,
        help="Existing writable workspace parent outside native temp/Codex-home exceptions",
    )
    parser.add_argument(
        "--report", type=Path, required=True, help="Local JSON evidence output"
    )
    parser.add_argument(
        "--timeout",
        type=bounded_timeout,
        default=180,
        help="Maximum seconds for each of three live sessions (15-900; default 180)",
    )
    args = parser.parse_args()
    os.umask(0o077)
    args.report = args.report.expanduser().absolute()
    if args.report.is_symlink() or (args.report.exists() and not args.report.is_file()):
        parser.error("--report must be a regular file path, not a symlink or directory")
    try:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=args.report.parent):
            pass
    except OSError as error:
        parser.error(f"--report parent must be writable: {error}")
    report = {
        "status": "failed",
        "sessions": [],
        "processes": [],
        "live_model_behavior": "unverified until every live gate assertion passes",
        "mock_responses": False,
    }
    started = time.monotonic()
    try:
        require(not args.report.is_symlink(), "--report must not be a symlink")
        Gate(args, report).execute()
        report["live_model_behavior"] = (
            "passed three real native model sessions and independent fixture assertions"
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=args.report.parent, delete=False
        ) as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
            temporary = Path(stream.name)
        temporary.replace(args.report)
        print(
            json.dumps(
                {
                    key: report[key]
                    for key in ("status", "error", "workspace")
                    if key in report
                }
                | {"report": str(args.report)}
            )
        )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
