#!/usr/bin/env python3
"""Check a built `codex team` command without authentication or model requests.

Requires Python 3.11+ for standard-library TOML validation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None


TASK = 'Inspect spaces, "quotes", $(touch never-created), and `printf surprise`\n-c payload'
ROLES = {
    "engineer": ("gpt-6-sol", "medium"),
    "gatekeeper": ("gpt-6-luna", "low"),
    "skeptic": ("gpt-6-astra", "high"),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_preview(
    plan: dict,
    *,
    workspace: Path,
    state: Path,
    exec_mode: bool,
    roles: dict[str, tuple[str, str]],
) -> list[str]:
    """Validate both routing and complete role configs, returning native argv."""
    require(tomllib is not None, "team smoke checks require Python 3.11+")
    require(isinstance(plan, dict), "preview must be a JSON object")
    argv = plan.get("argv")
    require(
        isinstance(argv, list) and all(isinstance(arg, str) for arg in argv),
        "preview argv must be an array of strings",
    )
    require(isinstance(plan.get("prompt"), str), "preview prompt must be a string")
    require(len(argv) >= 4 and argv[-2:] == ["--", plan["prompt"]], "invalid prompt routing")
    require(plan["prompt"].endswith(TASK), "literal task text changed")
    require((argv[1] == "exec") == exec_mode, "incorrect interactive/exec routing")
    require("-C" in argv[:-1] and "-m" in argv[:-1], "missing working directory or model routing")
    require(argv[argv.index("-C") + 1] == str(workspace), "working directory changed")
    require(argv[argv.index("-m") + 1] == "gpt-6.1-sol", "architect model changed")
    overrides = {}
    for index, arg in enumerate(argv[:-1]):
        if arg == "-c":
            key, value = argv[index + 1].split("=", 1)
            overrides[key] = tomllib.loads("value=" + value)["value"]
    require(overrides.get("model_reasoning_effort") == "ultra", "architect effort changed")
    require(overrides.get("agents.enabled") is True, "team agents are disabled")
    require(overrides.get("features.multi_agent") is True, "multi-agent tools are disabled")
    require(
        overrides.get("features.multi_agent_v2.disable_native_tools") is False,
        "native multi-agent tools are disabled",
    )
    files = plan.get("role_files")
    require(isinstance(files, dict) and len(files) == len(roles), "incomplete role configs")
    found = set()
    for filename, contents in files.items():
        path = Path(filename)
        require(path.is_absolute() and path.parent.parent == state, "role path escaped state directory")
        role = path.stem
        require(role in roles and role not in found, "unexpected or duplicate team role")
        require(overrides.get(f"agents.{role}.config_file") == filename, f"{role} config not routed")
        config = tomllib.loads(contents)
        model, effort = roles[role]
        require(config.get("name") == role, f"{role} name changed")
        require(config.get("model") == model, f"{role} model changed")
        require(config.get("model_reasoning_effort") == effort, f"{role} effort changed")
        require(bool(config.get("description")), f"{role} description missing")
        require(bool(config.get("developer_instructions")), f"{role} instructions missing")
        require(
            not {"sandbox_mode", "approval_policy"}.intersection(config),
            f"{role} overrides inherited permissions",
        )
        require(not path.exists(), "dry-run published a role file")
        found.add(role)
    require(not state.exists(), "dry-run created the state directory")
    return argv


def smoke(codex: Path) -> dict:
    require(tomllib is not None, "team smoke checks require Python 3.11+")
    codex = codex.expanduser().resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="codex-team-smoke-") as temporary:
        root = Path(temporary).resolve()
        workspace = root / "workspace with spaces"
        workspace.mkdir()
        home = root / "home"
        home.mkdir()
        state = root / "team state"
        env = os.environ.copy()
        env["CODEX_HOME"] = str(home)
        env["HOME"] = str(home)
        for key in ("CODEX_API_KEY", "OPENAI_API_KEY", "CODEX_ACCESS_TOKEN"):
            env.pop(key, None)

        def run(args: list[str]) -> str:
            result = subprocess.run(
                [str(codex), *args], cwd=workspace, env=env,
                capture_output=True, text=True, timeout=20, check=False,
            )
            require(result.returncode == 0, f"Codex failed ({result.returncode}): {result.stderr[:1000]}")
            return result.stdout

        require("team" in run(["--help"]).split(), "Codex help does not advertise team")
        help_text = run(["team", "--help"])
        for flag in ("--architect-model", "--architect-effort", "--prompt", "--exec", "--dry-run"):
            require(flag in help_text, f"team help omits {flag}")
        for exec_mode in (False, True):
            roles = dict(ROLES)
            args = [
                "--strict-config", "--profile", "smoke-root", "team", "--dry-run",
                "--cwd", str(workspace), "--state-dir", str(state),
                "--architect-model", "gpt-6.1-sol", "--architect-effort", "ultra",
                "--prompt", TASK, "--sandbox=read-only",
            ]
            if exec_mode:
                roles["engineer"] = ("smoke-engineer", "low")
                args.extend([
                    "--exec", "--json", "--ephemeral", "--skip-git-repo-check",
                    "--engineer-model", "smoke-engineer", "--engineer-effort", "low",
                    "--color", "never", "--output-last-message", str(root / "result.txt"),
                ])
            else:
                args.append("--no-alt-screen")
            plan = json.loads(run(args))
            argv = validate_preview(
                plan, workspace=workspace, state=state, exec_mode=exec_mode, roles=roles,
            )
            # --help parses the preview through the same installed native CLI
            # without loading configs, writing roles, or starting a session.
            run([*argv[1:-2], "--help"])
            require(not list(workspace.iterdir()), "preview modified the fixture workspace")
            require(not (root / "result.txt").exists(), "preview wrote the final output file")
        return {"status": "passed", "checks": 6, "model_requests": 0, "codex": str(codex)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", type=Path, required=True, help="built native Codex executable")
    args = parser.parse_args()
    try:
        print(json.dumps(smoke(args.codex), sort_keys=True))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Team smoke check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
