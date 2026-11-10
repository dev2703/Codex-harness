#!/usr/bin/env python3
"""Launch a Codex session with a persistent, configurable four-role team.

The lead is the root Codex session. The other three roles are native Codex
agent types, selected by the lead through spawn_agent when the task warrants it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Sequence


EFFORTS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
    "persistent",
)


@dataclass(frozen=True)
class RoleConfig:
    name: str
    description: str
    instructions: str
    model: str
    effort: str


@dataclass(frozen=True)
class TeamConfig:
    codex: str
    cwd: Path
    state_dir: Path
    architect_model: str
    architect_effort: str
    engineer_model: str
    engineer_effort: str
    gatekeeper_model: str
    gatekeeper_effort: str
    skeptic_model: str
    skeptic_effort: str
    prompt: str | None = None
    passthrough: tuple[str, ...] = ()


ROLE_TEXT = {
    "engineer": (
        "Autonomous production engineer for implementation, tests, and bounded shell work.",
        "Own the paths and task contract assigned by the architect. Implement production code "
        "and meaningful tests, run appropriate checks, and report changed files, commands, "
        "results, and remaining risks. Work autonomously inside the inherited sandbox and "
        "approval policy. Other agents may edit the same checkout; preserve their work and "
        "coordinate before touching paths outside your assigned ownership.",
    ),
    "gatekeeper": (
        "Fast advisory gatekeeper for narrow tool, path, and change reviews.",
        "Review the exact proposed tool arguments, target paths, and permissions for a "
        "bounded question. Run relevant cheap linters and checks when permitted. Return a concise allow, revise, or "
        "escalate recommendation with the reason. Escalate ambiguity or material risk to "
        "the architect. This advice does not replace or disable Codex sandbox, policy, "
        "approval, or automatic review enforcement. Favor speed but never claim a "
        "guaranteed response time.",
    ),
    "skeptic": (
        "Adversarial reviewer for diffs, boundary cases, races, and unsupported claims.",
        "Challenge the implementation and evidence. Inspect the diff, boundary inputs, "
        "race conditions, error paths, and security assumptions. Give actionable findings "
        "with file anchors, minimal reproductions, and severity. Distinguish verified "
        "failures from hypotheses. Do not edit the engineer's owned files unless the "
        "architect explicitly assigns a fix.",
    ),
}

ROOT_BRIEFING = """You are the lead architect and final arbiter for this task. For implementation tasks, define a task contract before delegating: task ID, goal, owned paths, scope, architectural invariants, checks, acceptance criteria, and required evidence. Obtain a gatekeeper review of the proposed tool parameters, paths, and relevant linters, delegate production code and shell work to the engineer, and obtain a skeptic review of the resulting diff and evidence before finalizing. The gatekeeper's advice never replaces native Codex enforcement and has no guaranteed latency. Ask the skeptic to attack diffs, fuzz boundary cases, and search for race conditions with actionable reproductions. Review architectural invariants, adjudicate disagreements, and settle merge conflicts while preserving others' work. Send fixes back to the engineer and repeat review until the acceptance criteria hold. Report the outcome with evidence and remaining limits. Use the native spawn_agent agent_type values engineer, gatekeeper, and skeptic with fresh contexts; full-history forks may reject role selection on V1. Reuse role agents for follow-up work. This team workflow explicitly requests those delegations, subject to higher-priority session policy. Informational and no-op tasks can be handled directly."""


def _toml_string(value: str) -> str:
    # JSON string escapes are also valid TOML basic-string escapes for these values.
    return json.dumps(value, ensure_ascii=False)


def _role_file(role: RoleConfig) -> str:
    return "".join(
        f"{key} = {_toml_string(value)}\n"
        for key, value in (
            ("name", role.name),
            ("description", role.description),
            ("developer_instructions", role.instructions),
            ("model", role.model),
            ("model_reasoning_effort", role.effort),
        )
    )


def _nonblank(value: str, label: str, *, model: bool = False) -> str:
    if not value.strip():
        raise ValueError(f"{label} must not be blank")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{label} contains a control character")
    if model and (
        len(value) > 256
        or any(char.isspace() for char in value)
        or value.startswith("-")
    ):
        raise ValueError(
            f"{label} must be a model slug without whitespace (up to 256 characters)"
        )
    return value


def _validate_passthrough(tokens: Sequence[str], *, exec_mode: bool) -> tuple[str, ...]:
    """Allow useful session flags without letting forwarding change team routing."""
    value_flags = {
        "--sandbox",
        "--profile",
        "--add-dir",
        "--image",
        "--local-provider",
        "--ask-for-approval",
        "-a",
        "--color",
        "--output-last-message",
        "-o",
        "--output-schema",
    }
    bool_flags = {
        "--oss",
        "--approve-for-me",
        "--dangerously-bypass-approvals-and-sandbox",
        "--no-alt-screen",
        "--search",
        "--no-daemon",
        "--strict-config",
        "--help",
        "-h",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
    }
    interactive_only = {
        "--ask-for-approval",
        "-a",
        "--no-alt-screen",
        "--search",
        "--no-daemon",
    }
    exec_only = {
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--color",
        "--output-last-message",
        "-o",
        "--output-schema",
    }
    result: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in (interactive_only if exec_mode else exec_only):
            mode = "exec" if exec_mode else "interactive"
            raise ValueError(f"unsupported Codex {mode} argument: {token}")
        if token in bool_flags:
            result.append(token)
            index += 1
        elif token in value_flags:
            if index + 1 >= len(tokens) or tokens[index + 1].startswith("-"):
                raise ValueError(f"{token} requires a value")
            result.extend((token, tokens[index + 1]))
            index += 2
        else:
            raise ValueError(f"unsupported forwarded Codex argument: {token}")
    return tuple(result)


def build_plan(config: TeamConfig, *, exec_mode: bool = False) -> dict[str, object]:
    """Return a deterministic launch preview without reading or writing state."""
    codex = _nonblank(config.codex, "--codex")
    cwd = config.cwd.expanduser().resolve()
    if not cwd.is_dir():
        raise ValueError(f"--cwd is not a directory: {cwd}")
    state_dir = config.state_dir.expanduser()
    if not state_dir.is_absolute():
        state_dir = cwd / state_dir
    state_dir = state_dir.resolve()
    models = {
        "architect": _nonblank(config.architect_model, "--architect-model", model=True),
        "engineer": _nonblank(config.engineer_model, "--engineer-model", model=True),
        "gatekeeper": _nonblank(
            config.gatekeeper_model, "--gatekeeper-model", model=True
        ),
        "skeptic": _nonblank(config.skeptic_model, "--skeptic-model", model=True),
    }
    for name in models:
        if getattr(config, f"{name}_effort") not in EFFORTS:
            raise ValueError(f"--{name}-effort must be one of: {', '.join(EFFORTS)}")
    roles = {
        name: RoleConfig(
            name, *ROLE_TEXT[name], models[name], getattr(config, f"{name}_effort")
        )
        for name in ROLE_TEXT
    }
    contents = {name: _role_file(role) for name, role in roles.items()}
    digest = hashlib.sha256(
        json.dumps(contents, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:20]
    role_dir = state_dir / digest
    role_files = {
        str(role_dir / f"{name}.toml"): contents[name] for name in sorted(contents)
    }
    prompt = ROOT_BRIEFING
    if config.prompt:
        prompt += "\n\nUser task:\n" + config.prompt
    else:
        prompt += "\n\nNo task supplied yet. Acknowledge the team configuration and wait for the user's task."
    argv = [
        codex,
        *(["exec"] if exec_mode else []),
        "-C",
        str(cwd),
        "-m",
        models["architect"],
        "-c",
        f"model_reasoning_effort={_toml_string(config.architect_effort)}",
        "-c",
        "features.multi_agent=true",
        "-c",
        "agents.enabled=true",
        "-c",
        "features.multi_agent_v2.disable_native_tools=false",
    ]
    for name in sorted(contents):
        role_path = str(role_dir / f"{name}.toml")
        argv.extend(
            (
                "-c",
                f"agents.{name}.description={_toml_string(ROLE_TEXT[name][0])}",
                "-c",
                f"agents.{name}.config_file={_toml_string(role_path)}",
            )
        )
    forwarded = _validate_passthrough(config.passthrough, exec_mode=exec_mode)
    argv.extend(forwarded)
    argv.extend(("--", prompt))
    return {
        "argv": argv,
        "role_files": role_files,
        "root_briefing": ROOT_BRIEFING,
        "prompt": prompt,
    }


def _reject_symlink_components(path: Path, root: Path) -> None:
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise ValueError(f"state path contains a symlink: {component}")


def _write_role_files(role_files: dict[str, str]) -> None:
    if not role_files:
        return
    # All files share one content-addressed directory and state root.
    root = Path(next(iter(role_files))).parent.parent
    for filename, content in role_files.items():
        path = Path(filename)
        if path.parent.parent != root:
            raise ValueError(
                f"role file is outside the selected state directory: {path}"
            )
        _reject_symlink_components(path, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink_components(path, root)
        data = content.encode("utf-8")
        # Publish only complete bytes. A second launcher sees either no file or
        # the full file, including when both launch the same content hash.
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".role-", delete=False
            ) as stream:
                temporary_path = Path(stream.name)
                os.chmod(temporary_path, 0o600)
                stream.write(data)
            try:
                os.link(temporary_path, path, follow_symlinks=False)
            except FileExistsError:
                if path.is_symlink() or not path.is_file() or path.read_bytes() != data:
                    raise ValueError(f"existing role file differs: {path}") from None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex", default="codex", help="Codex executable path or command"
    )
    parser.add_argument(
        "--cwd", type=Path, default=Path.cwd(), help="session working directory"
    )
    parser.add_argument(
        "--state-dir", type=Path, help="persistent role config directory"
    )
    for name, default_model, default_effort in (
        ("architect", "gpt-6.1-sol", "ultra"),
        ("engineer", "gpt-6-sol", "medium"),
        ("gatekeeper", "gpt-6-luna", "low"),
        ("skeptic", "gpt-6-astra", "high"),
    ):
        parser.add_argument(f"--{name}-model", default=default_model)
        parser.add_argument(f"--{name}-effort", choices=EFFORTS, default=default_effort)
    parser.add_argument("--prompt", help="task for the lead architect")
    parser.add_argument(
        "--exec",
        dest="exec_mode",
        action="store_true",
        help="run Codex non-interactively",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print JSON preview; do not launch"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    forwarded: tuple[str, ...] = ()
    if "--" in args:
        separator = args.index("--")
        forwarded = tuple(args[separator + 1 :])
        args = args[:separator]
    parser = _parser()
    parsed = parser.parse_args(args)
    cwd = parsed.cwd.expanduser().resolve()
    config = TeamConfig(
        codex=parsed.codex,
        cwd=cwd,
        state_dir=parsed.state_dir or cwd / ".codex-harness" / "teams",
        architect_model=parsed.architect_model,
        architect_effort=parsed.architect_effort,
        engineer_model=parsed.engineer_model,
        engineer_effort=parsed.engineer_effort,
        gatekeeper_model=parsed.gatekeeper_model,
        gatekeeper_effort=parsed.gatekeeper_effort,
        skeptic_model=parsed.skeptic_model,
        skeptic_effort=parsed.skeptic_effort,
        prompt=parsed.prompt,
        passthrough=forwarded,
    )
    try:
        plan = build_plan(config, exec_mode=parsed.exec_mode)
        if parsed.dry_run:
            print(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        _write_role_files(plan["role_files"])
        return subprocess.run(plan["argv"], check=False).returncode
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
