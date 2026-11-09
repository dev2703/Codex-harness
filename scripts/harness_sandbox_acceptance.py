#!/usr/bin/env python3
"""Exercise a built native CLI in a mandatory, offline Bubblewrap sandbox.

The loopback Responses fixture checks process wiring, user-context transport,
and one fixed native shell write denial under the read-only sandbox.
It is deterministic test data, not a model or evidence of live-model behavior.
Fetch dependencies and the pinned Jev objects before invoking this runner.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

MAX_OUTPUT = 128 * 1024
READONLY_CALL_ID = "harness-readonly-write"
READONLY_MARKER = "HARNESS_READONLY_SHELL_STARTED"
READONLY_DESTINATION = Path("/fixture/native-readonly-must-not-exist")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_reference_runner():
    path = Path(__file__).with_name("harness_jev_reference.py")
    spec = importlib.util.spec_from_file_location("harness_jev_reference", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sandbox_command(
    bwrap: str, binary: Path, payload: Path, fixture: Path
) -> list[str]:
    args = [
        bwrap,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
    ]
    mounted = []
    for directory in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(directory).exists():
            args += ["--ro-bind", directory, directory]
            mounted.append(Path(directory))
    interpreter = Path(sys.executable).resolve()
    for path in (Path(sys.base_prefix).resolve(), interpreter):
        if not any(path == parent or parent in path.parents for parent in mounted):
            args += ["--ro-bind", str(path), str(path)]
            mounted.append(path)
    args += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/home/sandbox",
        "--dir",
        "/app",
        "--ro-bind",
        str(binary),
        "/app/codex",
        "--ro-bind",
        str(Path(__file__).resolve().parent),
        "/scripts",
        "--ro-bind",
        str(payload),
        "/input.json",
        "--bind",
        str(fixture),
        "/fixture",
        "--clearenv",
        "--setenv",
        "HOME",
        "/home/sandbox",
        "--setenv",
        "CODEX_HOME",
        "/home/sandbox/.codex",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "LC_ALL",
        "C.UTF-8",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--chdir",
        "/fixture",
        str(interpreter),
        "-I",
        "/scripts/harness_sandbox_acceptance.py",
        "--worker",
        "/input.json",
    ]
    return args


def verify_isolation(payload: dict) -> dict:
    namespace = os.readlink("/proc/self/ns/net")
    interfaces = [name for _, name in socket.if_nameindex()]
    require(
        namespace != payload["outer_netns"] and interfaces == ["lo"],
        "a distinct loopback-only network namespace is required",
    )
    require(
        not any(
            "KEY" in name or "TOKEN" in name or "PROXY" in name for name in os.environ
        ),
        "provider credentials/proxies must not enter the sandbox",
    )
    require(not list(Path.home().iterdir()), "sandbox HOME must start empty")
    require(
        os.geteuid() != 0,
        "run acceptance as an unprivileged user for meaningful permission checks",
    )
    require(
        not (Path("/host").exists() or Path("/run/user").exists()),
        "unexpected host runtime mount",
    )
    try:
        with Path("/app/codex").open("ab"):
            pass
    except OSError:
        pass
    else:
        raise RuntimeError("binary mount must be read-only")
    return {
        "network_namespace_changed": True,
        "interfaces": interfaces,
        "isolated_home": True,
        "host_credentials_mounted": False,
        "binary_mount_read_only": True,
        "fixture_only_host_write_mount": True,
    }


class Processes:
    def __init__(self):
        self.records = []
        self.lock = threading.Lock()

    def run(
        self, args: list[str], *, accepted=(0,), contains=None, timeout=45
    ) -> subprocess.CompletedProcess:
        started = time.monotonic()
        process = subprocess.Popen(
            args,
            cwd="/fixture",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
            self.record(args, process.returncode, stdout, stderr, started, timeout=True)
            raise RuntimeError(f"process timed out: {args}")
        self.record(args, process.returncode, stdout, stderr, started)
        if accepted is not None:
            require(
                process.returncode in accepted,
                f"unexpected exit {process.returncode}: {args}; {stderr[-2000:]}",
            )
        if contains is not None:
            require(
                contains in stdout + stderr,
                f"missing expected diagnostic {contains!r}: {args}",
            )
        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)

    def record(self, args, code, stdout, stderr, started, **extra):
        with self.lock:
            self.records.append(
                {
                    "argv": args,
                    "exit_code": code,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "stdout": stdout[-MAX_OUTPUT:],
                    "stderr": stderr[-MAX_OUTPUT:],
                    "output_truncated": len(stdout) > MAX_OUTPUT
                    or len(stderr) > MAX_OUTPUT,
                    **extra,
                }
            )

    def codex(self, *args, **kwargs):
        return self.run(["/app/codex", "-C", "/fixture", *args], **kwargs)

    def git(self, *args):
        return self.run(
            [
                "git",
                "-c",
                "user.name=Acceptance Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                *args,
            ]
        )


def advertised_exec_namespace(tools: list, namespace=None):
    for tool in tools:
        if tool.get("type") == "function" and tool.get("name") == "exec_command":
            return True, namespace
        if tool.get("type") == "namespace":
            found, child_namespace = advertised_exec_namespace(
                tool.get("tools", []), tool.get("name")
            )
            if found:
                return found, child_namespace
    return False, None


def readonly_tool_result(request: dict) -> dict:
    outputs = [
        item
        for item in request.get("input", [])
        if item.get("type") == "function_call_output"
        and item.get("call_id") == READONLY_CALL_ID
    ]
    require(len(outputs) == 1, "native read-only shell returned no unique tool result")
    output = outputs[0].get("output")
    require(isinstance(output, str), "native read-only shell output must contain text")
    require(
        re.search(rf"(?m)^{re.escape(READONLY_MARKER)}\r?$", output) is not None,
        "native shell did not start; infrastructure failures do not prove sandbox denial",
    )
    exited = re.search(r"Process exited with code (-?\d+)", output)
    require(
        exited is not None and int(exited.group(1)) != 0,
        "native read-only write must terminate with a nonzero command exit",
    )
    require(
        any(
            reason in output.lower()
            for reason in (
                "permission denied",
                "operation not permitted",
                "read-only file system",
            )
        ),
        "native shell returned no filesystem permission denial",
    )
    return {
        "call_id": READONLY_CALL_ID,
        "shell_started": True,
        "exit_code": int(exited.group(1)),
        "output": output,
    }


class ResponsesFixture:
    """Fixed Responses streams reachable only inside the sandbox."""

    def __init__(self, *, readonly_probe=False):
        self.requests = []
        self.errors = []
        self.readonly_probe = readonly_probe
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                try:
                    require(self.path == "/v1/responses", "unexpected fixture endpoint")
                    require(
                        not self.headers.get("Authorization"),
                        "fixture must not receive credentials",
                    )
                    require(
                        not self.headers.get("Content-Encoding"),
                        "fixture requires uncompressed requests",
                    )
                    length = int(self.headers.get("Content-Length", "0"))
                    require(
                        0 < length < 4 * 1024 * 1024, "invalid fixture request size"
                    )
                    owner.requests.append(json.loads(self.rfile.read(length)))
                    number = len(owner.requests)
                    events = [
                        {
                            "type": "response.created",
                            "response": {"id": f"offline-{number}"},
                        },
                        {
                            "type": "response.output_item.done",
                            "item": {
                                "type": "message",
                                "role": "assistant",
                                "id": f"message-{number}",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": "Offline handoff transport verified.",
                                    }
                                ],
                            },
                        },
                        {
                            "type": "response.completed",
                            "response": {
                                "id": f"offline-{number}",
                                "usage": {
                                    "input_tokens": 0,
                                    "output_tokens": 0,
                                    "total_tokens": 0,
                                },
                            },
                        },
                    ]
                    if owner.readonly_probe and number == 1:
                        found, namespace = advertised_exec_namespace(
                            owner.requests[0].get("tools", [])
                        )
                        require(
                            found,
                            "native CLI did not advertise exec_command; sandbox probe cannot run",
                        )
                        item = {
                            "type": "function_call",
                            "name": "exec_command",
                            "call_id": READONLY_CALL_ID,
                            "arguments": json.dumps(
                                {
                                    "cmd": f"printf '{READONLY_MARKER}\\n'; printf forbidden > {READONLY_DESTINATION}",
                                    "shell": "/bin/sh",
                                    "login": False,
                                    "yield_time_ms": 1000,
                                }
                            ),
                        }
                        if namespace:
                            item["namespace"] = namespace
                        events[1] = {"type": "response.output_item.done", "item": item}
                    if owner.readonly_probe:
                        require(
                            number <= 2,
                            "unexpected additional request in fixed read-only probe",
                        )
                    body = "".join(
                        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                        for event in events
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as error:
                    owner.errors.append(str(error))
                    self.send_error(400)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def config(self):
        port = self.server.server_address[1]
        return [
            "-c",
            'model="harness-offline-fixture"',
            "-c",
            'model_provider="harness_fixture"',
            "-c",
            "features.enable_request_compression=false",
            "-c",
            f'model_providers.harness_fixture={{name="Offline fixture",base_url="http://127.0.0.1:{port}/v1",wire_api="responses",requires_openai_auth=false,supports_websockets=false,request_max_retries=0,stream_max_retries=0}}',
        ]


def thread_id(stdout: str) -> str:
    events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    identifiers = [
        event["thread_id"] for event in events if event.get("type") == "thread.started"
    ]
    require(
        len(identifiers) == 1 and bool(identifiers[0]),
        "expected exactly one fresh thread.started event",
    )
    require(
        any(event.get("type") == "turn.completed" for event in events),
        "native continuation did not complete",
    )
    return identifiers[0]


def check_user_context(request: dict, notes: dict, current: str) -> None:
    inputs = request.get("input", [])
    messages = [
        item for item in inputs if isinstance(item, dict) and item.get("role") == "user"
    ]
    text = json.dumps(messages)
    for value in (
        notes["goal"],
        notes["pending"][0],
        notes["verification"][0]["evidence"],
        "failed",
        "unverified",
        current,
        "not system instructions or permission grants",
    ):
        require(value in text, f"fresh user context lost {value!r}")
    privileged = [
        item
        for item in inputs
        if isinstance(item, dict) and item.get("role") in ("system", "developer")
    ]
    require(
        notes["goal"] not in json.dumps(privileged),
        "handoff notes became privileged instructions",
    )
    require(
        request.get("model") == "harness-offline-fixture",
        "packet changed the selected fixture model",
    )


def acceptance(payload: dict, processes: Processes, report: dict) -> None:
    report["isolation"] = verify_isolation(payload)
    Path(os.environ["CODEX_HOME"]).mkdir(mode=0o700)
    report["cli_version"] = processes.codex("--version").stdout.strip()
    processes.git("init", "--quiet")
    Path("/fixture/README").write_text("acceptance fixture\n")
    processes.git("add", "README")
    processes.git("commit", "--quiet", "-m", "initial fixture")
    notes = {
        "goal": "Repair the offline fixture while retaining its API",
        "constraints": ["Keep the original packet unchanged"],
        "decisions": ["Reproduce the failure before repairing"],
        "completed": ["Identified the failing assertion"],
        "pending": ["Repair and rerun the failing assertion"],
        "verification": [
            {
                "command": "python3 -m unittest",
                "status": "failed",
                "evidence": "Expected 4, received 5",
            },
            {
                "command": "cargo test",
                "status": "unverified",
                "evidence": "Not executed",
            },
        ],
    }
    notes_path = Path("/fixture/notes.json")
    notes_path.write_text(json.dumps(notes))
    processes.codex(
        "handoff",
        "save",
        "--notes",
        "notes.json",
        "--output",
        "packet.json",
        "--source-session",
        "previous-run-reference",
    )
    packet_path = Path("/fixture/packet.json")
    original = packet_path.read_bytes()
    packet = json.loads(original)
    require(
        stat.S_IMODE(packet_path.stat().st_mode) == 0o600,
        "published packet must have mode 0600",
    )
    require(packet["payload"]["notes"] == notes, "saved packet lost typed notes")
    require(
        packet["payload"]["source_session"] == "previous-run-reference",
        "source reference lost",
    )
    require(
        packet["payload"]["repository_head"]
        == processes.git("rev-parse", "HEAD").stdout.strip(),
        "repository provenance lost",
    )
    notes_path.unlink()
    inspected = processes.codex("handoff", "inspect", "packet.json")
    inspection = json.loads(inspected.stdout)
    require(
        inspection["status"] == "valid"
        and inspection["verification_count"] == 2
        and inspection["pending_count"] == 1,
        "fresh inspection lost verification/pending counts",
    )
    require(notes["goal"] not in inspected.stdout, "inspect printed private notes")
    report["cases"].append(
        "save_exit_remove_notes_fresh_inspect_permissions_and_provenance"
    )

    notes_path.write_text(json.dumps(notes))
    processes.codex(
        "handoff",
        "save",
        "--notes",
        "notes.json",
        "--output",
        "packet.json",
        accepted=None,
    )
    require(
        processes.records[-1]["exit_code"] != 0
        and packet_path.read_bytes() == original,
        "overwrite must fail without modifying the packet",
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(
            pool.map(
                lambda _: processes.codex(
                    "handoff",
                    "save",
                    "--notes",
                    "notes.json",
                    "--output",
                    "concurrent.json",
                    accepted=None,
                ),
                range(6),
            )
        )
    require(
        sum(outcome.returncode == 0 for outcome in outcomes) == 1,
        "exactly one concurrent publisher must succeed",
    )
    processes.codex("handoff", "inspect", "concurrent.json")
    report["cases"].append("overwrite_refusal_and_six_concurrent_process_publishers")

    # Kill real CLI publishers at different startup offsets. Published destinations
    # must always be either absent or fully loadable; temporary files are not packets.
    interruptions = []
    for index, delay in enumerate((0, 0.001, 0.005, 0.02, 0.05)):
        args = [
            "/app/codex",
            "-C",
            "/fixture",
            "handoff",
            "save",
            "--notes",
            "notes.json",
            "--output",
            f"interrupted-{index}.json",
        ]
        started = time.monotonic()
        process = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        time.sleep(delay)
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        stdout, stderr = process.communicate(timeout=10)
        processes.record(
            args,
            process.returncode,
            stdout,
            stderr,
            started,
            interruption_delay_seconds=delay,
        )
        path = Path(f"/fixture/interrupted-{index}.json")
        if path.exists():
            processes.codex("handoff", "inspect", path.name)
        interruptions.append(
            {
                "delay_seconds": delay,
                "exit_code": process.returncode,
                "published_complete_packet": path.exists(),
            }
        )
    report["interrupted_publishers"] = interruptions
    report["cases"].append("interrupted_publishers_never_publish_truncated_packets")

    failures = []
    corrupt = copy.deepcopy(packet)
    corrupt["payload"]["notes"]["pending"][0] = "Changed without checksum"
    failures.append(("corrupt.json", corrupt, "checksum mismatch"))
    future = copy.deepcopy(packet)
    future["version"] = 999
    failures.append(("future.json", future, "unsupported handoff version"))
    for field in ("model", "sandbox", "approval_policy"):
        authority = copy.deepcopy(packet)
        authority["payload"][field] = "must-not-import"
        failures.append((f"authority-{field}.json", authority, "unknown field"))
    for name, value, diagnostic in failures:
        Path("/fixture", name).write_text(json.dumps(value))
        for verb in ("inspect", "continue"):
            result = processes.codex(
                "handoff", verb, name, accepted=None, contains=diagnostic
            )
            require(
                result.returncode != 0 and "thread.started" not in result.stdout,
                "invalid packet must fail before session startup",
            )
    Path("/fixture/oversized.json").write_bytes(b"x" * (64 * 1024 + 1))
    Path("/fixture/packet-link.json").symlink_to("packet.json")
    os.mkfifo("/fixture/input.fifo")
    for name, diagnostic in (
        ("oversized.json", "exceeds 64 KiB"),
        ("packet-link.json", "must not be a symlink"),
        ("input.fifo", "must be a regular file"),
        (".", "must be a regular file"),
    ):
        for verb in ("inspect", "continue"):
            result = processes.codex(
                "handoff", verb, name, accepted=None, contains=diagnostic
            )
            require(
                result.returncode != 0 and "thread.started" not in result.stdout,
                "nonregular/oversized packet started a session",
            )
    for field in ("model", "sandbox", "approval_policy"):
        bad_notes = {**notes, field: "must-not-import"}
        notes_path.write_text(json.dumps(bad_notes))
        result = processes.codex(
            "handoff",
            "save",
            "--notes",
            "notes.json",
            "--output",
            "invalid.json",
            accepted=None,
            contains="unknown field",
        )
        require(
            result.returncode != 0 and not Path("/fixture/invalid.json").exists(),
            "authority notes were published",
        )
    notes_path.write_text(json.dumps({**notes, "goal": "x" * (32 * 1024)}))
    result = processes.codex(
        "handoff",
        "save",
        "--notes",
        "notes.json",
        "--output",
        "invalid.json",
        accepted=None,
        contains="exceed 32 KiB",
    )
    require(result.returncode != 0, "oversized typed notes were accepted")
    notes_path.write_text(json.dumps(notes))
    Path("/fixture/notes-link.json").symlink_to("notes.json")
    for destination in ("packet-link.json", "notes-output.json"):
        source = (
            "notes-link.json" if destination == "notes-output.json" else "notes.json"
        )
        result = processes.codex(
            "handoff", "save", "--notes", source, "--output", destination, accepted=None
        )
        require(result.returncode != 0, "symlink input/output was accepted")
    require(
        packet_path.read_bytes() == original,
        "symlink/invalid cases changed original packet",
    )
    report["cases"].append(
        "corruption_version_size_symlink_fifo_directory_and_authority_rejections"
    )

    packet_path.chmod(0)
    try:
        result = processes.codex("handoff", "inspect", "packet.json", accepted=None)
        require(result.returncode != 0, "unreadable packet must fail")
    finally:
        packet_path.chmod(0o600)
    restricted = Path("/fixture/restricted")
    restricted.mkdir(mode=0o500)
    try:
        result = processes.codex(
            "handoff",
            "save",
            "--notes",
            "notes.json",
            "--output",
            "restricted/packet.json",
            accepted=None,
        )
        require(
            result.returncode != 0 and not (restricted / "packet.json").exists(),
            "publication in unwritable directory must fail",
        )
    finally:
        restricted.chmod(0o700)
    report["cases"].append("unreadable_input_and_unwritable_destination_permissions")

    # Two independent native exec processes receive the same portable packet.
    # No tool calls or model reasoning are synthesized or claimed as verified.
    with ResponsesFixture() as server:
        config = server.config()
        common = [
            "--sandbox",
            "read-only",
            "--json",
            "--ignore-user-config",
            "--ignore-rules",
        ]
        request = "Check the preserved failed and unverified evidence"
        first = processes.codex(
            *config, "handoff", "continue", "packet.json", *common, request, timeout=90
        )
        require(
            len(server.requests) == 1 and not server.errors,
            f"fixture protocol failed: {server.errors}",
        )
        check_user_context(server.requests[0], notes, request)
        first_thread = thread_id(first.stdout)
        Path("/fixture/README").write_text("changed repository HEAD\n")
        processes.git("commit", "--quiet", "-a", "-m", "changed fixture")
        mismatch = processes.codex(
            *config,
            "handoff",
            "continue",
            "packet.json",
            *common,
            accepted=None,
            contains="repository HEAD differs",
        )
        require(
            mismatch.returncode != 0
            and "thread.started" not in mismatch.stdout
            and len(server.requests) == 1,
            "repository mismatch reached the provider",
        )
        second = processes.codex(
            *config,
            "handoff",
            "continue",
            "packet.json",
            "--allow-repository-change",
            *common,
            request,
            timeout=90,
        )
        require(
            len(server.requests) == 2 and not server.errors,
            f"fixture protocol failed: {server.errors}",
        )
        check_user_context(server.requests[1], notes, request)
        second_thread = thread_id(second.stdout)
        require(
            first_thread != second_thread,
            "handoff continuation reused a native session",
        )
        report["offline_transport"] = {
            "provider_requests": 2,
            "thread_ids": [first_thread, second_thread],
            "packet_notes_user_role": True,
            "selected_model_preserved": True,
            "real_model_calls": 0,
            "mock_responses": True,
        }
    require(
        packet_path.read_bytes() == original, "continuations changed original packet"
    )
    report["cases"].append(
        "fresh_continue_user_context_distinct_sessions_repository_mismatch_and_acknowledgement"
    )

    require(
        not READONLY_DESTINATION.exists(),
        "native sandbox probe destination already exists",
    )
    with ResponsesFixture(readonly_probe=True) as server:
        probe = processes.codex(
            *server.config(),
            "-c",
            'approval_policy="never"',
            "-c",
            "features.code_mode=false",
            "-c",
            "features.unified_exec=true",
            "-c",
            "features.shell_snapshot_v2=false",
            "handoff",
            "continue",
            "packet.json",
            "--allow-repository-change",
            *common,
            "Exercise the fixed read-only shell fixture",
            accepted=None,
            timeout=90,
        )
        require(
            not server.errors,
            f"native sandbox fixture protocol failed: {server.errors}",
        )
        require(
            probe.returncode == 0 and len(server.requests) == 2,
            "native sandbox probe did not complete its two-request tool cycle",
        )
        result = readonly_tool_result(server.requests[1])
        require(
            not READONLY_DESTINATION.exists(),
            "native read-only tool created the forbidden fixture file",
        )
        require(
            packet_path.read_bytes() == original,
            "native sandbox probe changed the handoff packet",
        )
        report["native_readonly_tool"] = {
            **result,
            "thread_id": thread_id(probe.stdout),
            "destination_absent": True,
            "sandbox": "read-only",
            "approval_policy": "never",
            "mock_responses": True,
            "real_model_calls": 0,
        }
        report["native_tool_sandbox_behavior"] = (
            "passed fixed native shell write-denial process case; live-model behavior unverified"
        )
    report["cases"].append(
        "native_readonly_shell_started_denied_write_and_destination_absent"
    )

    jev = load_reference_runner()
    reference_result = jev.evaluate(payload["jev"])
    require(
        reference_result["passed"] == reference_result["case_count"],
        "Jev deterministic cases failed",
    )
    require(
        reference_result["model_calls"] == 0
        and reference_result["command_strings_executed"] == 0,
        "Jev reference violated the deterministic boundary",
    )
    report["jev"] = reference_result
    processes.run(
        [sys.executable, "-I", "/scripts/test_harness_jev_reference.py"], timeout=60
    )
    report["cases"].append("pinned_jev_deterministic_cases_and_guard_tests")


def worker(path: Path) -> int:
    payload = json.loads(path.read_text())
    processes = Processes()
    report = {
        "status": "failed",
        "source_revision": payload["source_revision"],
        "binary_sha256": payload["binary_sha256"],
        "cases": [],
        "live_model_behavior": "unverified; offline Responses fixture only",
        "native_tool_sandbox_behavior": "unverified; native read-only shell probe has not passed",
        "usage_metrics": "fixture zeros do not represent model usage",
    }
    started = time.monotonic()
    try:
        acceptance(payload, processes, report)
        report["status"] = "passed"
    except Exception as error:
        report["error"] = str(error)
    finally:
        report["processes"] = processes.records
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex", type=Path, help="Native codex binary built from this checkout"
    )
    parser.add_argument(
        "--reference",
        type=Path,
        help="Local Git checkout containing pinned Jev objects",
    )
    parser.add_argument(
        "--report", type=Path, help="Write JSON evidence here, including on failure"
    )
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker)
    if not args.codex or not args.reference:
        parser.error("--codex and --reference are required")
    report = {"status": "failed", "cases": [], "error": "runner did not start"}
    try:
        bwrap = shutil.which("bwrap")
        require(
            sys.platform == "linux" and bool(bwrap),
            "Linux Bubblewrap is required; unisolated fallback is prohibited",
        )
        binary = args.codex.resolve(strict=True)
        require(
            binary.is_file() and os.access(binary, os.X_OK),
            "--codex must be an executable native binary",
        )
        with binary.open("rb") as stream:
            require(
                stream.read(4) == b"\x7fELF",
                "--codex must be a native Linux ELF binary",
            )
            stream.seek(0)
            binary_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        jev = load_reference_runner()
        fixture = json.loads(
            Path(__file__)
            .with_name("fixtures")
            .joinpath("jev-deterministic-reference.json")
            .read_text()
        )
        jev.validate_fixture(fixture)
        revision = subprocess.check_output(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(Path(__file__).resolve().parent.parent),
                "rev-parse",
                "HEAD",
            ],
            text=True,
        ).strip()
        payload = {
            "source_revision": revision,
            "binary_sha256": binary_sha256,
            "outer_netns": os.readlink("/proc/self/ns/net"),
            "jev": {
                "fixture": fixture,
                "source": jev.committed_file(args.reference, "jev_gate.py"),
                "policy": json.loads(jev.committed_file(args.reference, "policy.json")),
            },
        }
        with tempfile.TemporaryDirectory(prefix="harness-sandbox-") as directory:
            root = Path(directory)
            fixture_path = root / "fixture"
            fixture_path.mkdir(mode=0o700)
            payload_path = root / "input.json"
            payload_path.write_text(json.dumps(payload))
            payload_path.chmod(0o600)
            command = sandbox_command(bwrap, binary, payload_path, fixture_path)
            process = subprocess.run(
                command, capture_output=True, text=True, timeout=600, env={}
            )
            try:
                report = json.loads(process.stdout)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    "sandbox produced no valid report: " + process.stderr[-2000:]
                ) from error
            report["sandbox_argv"] = command
            require(
                process.returncode in (0, 1),
                "sandbox process failed: " + process.stderr[-2000:],
            )
            require(
                report.get("status") == "passed" and process.returncode == 0,
                report.get("error", "acceptance failed"),
            )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        report["status"] = "failed"
        report["error"] = str(error)
    finally:
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(encoded)
        print(encoded, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
