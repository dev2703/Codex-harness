#!/usr/bin/env python3
"""Run the harness ship gates; dependency preparation is separate from offline acceptance."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from harness_jev_reference import REFERENCE, REVISION, validate_fixture

ROOT = Path(__file__).resolve().parents[1]
RUST = ROOT / "codex-rs"


def run(
    command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None
) -> None:
    print(f"[{cwd}] {shlex.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def checks(report_dir: Path) -> None:
    run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "scripts",
            "-p",
            "test_harness_*.py",
        ]
    )
    run(
        ["cargo", "fmt", "--", "--config", "imports_granularity=Item", "--check"],
        cwd=RUST,
    )
    # This exercises the production policy without treating it as a native CLI test.
    replay = report_dir / "harness-replay"
    replay_tests = report_dir / "harness-replay-tests"
    run(
        [
            "rustc",
            "--edition",
            "2024",
            "--test",
            "scripts/harness-replay.rs",
            "-o",
            str(replay_tests),
        ]
    )
    run([str(replay_tests)])
    run(["rustc", "--edition", "2024", "scripts/harness-replay.rs", "-o", str(replay)])
    run([str(replay)])


def native() -> None:
    run(["cargo", "build", "--locked", "-p", "codex-cli", "--bin", "codex"], cwd=RUST)
    for package, target, test_filter in (
        ("codex-cli", "--lib", "handoff"),
        ("codex-cli", "--bin", "handoff_cmd"),
        ("codex-core", "--lib", "tools::failure_policy::tests"),
        ("codex-core", "--lib", "tools::failure_observation::tests"),
        ("codex-core", "--lib", "tools::context::tests"),
        ("codex-core", "--lib", "agent::child_config::tests"),
    ):
        command = ["cargo", "test", "--locked", "-p", package, target]
        if target == "--bin":
            command.append("codex")
        run([*command, test_filter], cwd=RUST)
    run(["cargo", "test", "--locked", "-p", "codex-features", "--lib"], cwd=RUST)
    run(
        [
            "cargo",
            "clippy",
            "--locked",
            "-p",
            "codex-core",
            "-p",
            "codex-cli",
            "-p",
            "codex-features",
            "--lib",
            "--",
            "-D",
            "warnings",
        ],
        cwd=RUST,
    )
    run(
        [
            "cargo",
            "clippy",
            "--locked",
            "-p",
            "codex-cli",
            "--bin",
            "codex",
            "--",
            "-D",
            "warnings",
        ],
        cwd=RUST,
    )
    schema = RUST / "core/config.schema.json"
    expected = schema.read_bytes()
    run(
        [
            "cargo",
            "run",
            "--locked",
            "-p",
            "codex-config-schema",
            "--bin",
            "codex-write-config-schema",
        ],
        cwd=RUST,
    )
    if schema.read_bytes() != expected:
        run(["git", "diff", "--", "codex-rs/core/config.schema.json"])
        raise RuntimeError("generated config schema differs from the committed bytes")


def prepare_reference(reference: Path) -> None:
    fixture = json.loads(
        (ROOT / "scripts/fixtures/jev-deterministic-reference.json").read_text()
    )
    validate_fixture(fixture)
    # No checkout, package install, fixture command execution, hooks or branch lookup.
    reference.mkdir(parents=True, exist_ok=False)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(reference),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    git = ["git", "-c", "core.hooksPath=/dev/null", "-C", str(reference)]
    run([*git, "init", "--quiet"], env=env)
    run(
        [
            *git,
            "fetch",
            "--no-tags",
            "--depth=1",
            f"https://github.com/{REFERENCE}.git",
            REVISION,
        ],
        env=env,
    )
    run([*git, "cat-file", "-e", f"{REVISION}^{{commit}}"], env=env)


def acceptance(codex: Path, reference: Path, report_dir: Path) -> None:
    # The acceptance runner mandates Linux Bubblewrap, clears credentials, checks
    # the network namespace and uses only local mock Responses for continuation.
    run(
        [
            sys.executable,
            "scripts/harness_sandbox_acceptance.py",
            "--codex",
            str(codex),
            "--reference",
            str(reference),
            "--report",
            str(report_dir / "sandbox-acceptance.json"),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("checks", "native", "prepare-reference", "acceptance", "all"),
        required=True,
    )
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--codex", type=Path)
    args = parser.parse_args()
    report_dir = args.report_dir.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    if (
        args.phase in ("prepare-reference", "acceptance", "all")
        and args.reference is None
    ):
        parser.error("--reference is required for this phase")
    if args.phase in ("acceptance", "all") and args.codex is None:
        parser.error("--codex is required for this phase")
    if args.phase in ("checks", "all"):
        checks(report_dir)
    if args.phase in ("native", "all"):
        native()
    if args.phase == "prepare-reference":
        prepare_reference(args.reference.resolve())
    if args.phase in ("acceptance", "all"):
        acceptance(
            args.codex.resolve(strict=True),
            args.reference.resolve(strict=True),
            report_dir,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Harness gate failed: {error}", file=sys.stderr)
        raise SystemExit(1)
