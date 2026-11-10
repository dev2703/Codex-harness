"""Exercise team routing and durable role files without making model requests."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 can still run the launcher/process checks.
    tomllib = None


LAUNCHER = Path(__file__).with_name("harness_team.py")
DEFAULTS = {
    "engineer": ("gpt-6-sol", "medium"),
    "gatekeeper": ("gpt-6-luna", "low"),
    "skeptic": ("gpt-6-astra", "high"),
}


class TeamLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="harness-team-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace with spaces"
        self.workspace.mkdir()
        self.state = self.root / "state"

    def run_launcher(self, *args, expected=0):
        result = subprocess.run(
            [
                sys.executable,
                str(LAUNCHER),
                "--cwd",
                str(self.workspace),
                "--state-dir",
                str(self.state),
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def preview(self, *args):
        return json.loads(self.run_launcher("--dry-run", *args).stdout)

    def fake_codex(self, exit_code=0):
        executable = self.root / "fake codex"
        # A real child process proves argv forwarding and publication ordering.
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n"
            f"sys.exit({exit_code})\n"
        )
        executable.chmod(0o700)
        return str(executable)

    @staticmethod
    def config_overrides(argv):
        if tomllib is None:
            raise unittest.SkipTest("TOML round trips require Python 3.11+")
        overrides = {}
        for index, argument in enumerate(argv[:-1]):
            if argument == "-c":
                key, value = argv[index + 1].split("=", 1)
                overrides[key] = tomllib.loads("value=" + value)["value"]
        return overrides

    def test_default_routing_and_no_dry_run_side_effects(self):
        plan = self.preview("--codex", "missing-codex", "--prompt", "Fix the race")
        argv = plan["argv"]
        self.assertEqual(argv[0], "missing-codex")
        self.assertEqual(argv[argv.index("-C") + 1], str(self.workspace))
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-6.1-sol")
        self.assertIn("Fix the race", argv[-1])
        self.assertIn("task contract", plan["root_briefing"])
        self.assertFalse(self.state.exists())
        overrides = self.config_overrides(argv)
        self.assertEqual(overrides["model_reasoning_effort"], "ultra")
        self.assertTrue(overrides["agents.enabled"])
        for role, (model, effort) in DEFAULTS.items():
            path = overrides[f"agents.{role}.config_file"]
            content = tomllib.loads(plan["role_files"][path])
            self.assertEqual(content["name"], role)
            self.assertEqual(content["model"], model)
            self.assertEqual(content["model_reasoning_effort"], effort)
            self.assertNotIn("sandbox_mode", content)
            self.assertNotIn("approval_policy", content)
        self.assertNotIn("developer_instructions", overrides)

    def test_every_model_and_effort_can_be_overridden(self):
        args = ["--architect-model", "custom-lead", "--architect-effort", "high"]
        for role in DEFAULTS:
            args.extend(
                [f"--{role}-model", f"custom-{role}", f"--{role}-effort", "low"]
            )
        plan = self.preview(*args)
        self.assertEqual(plan["argv"][plan["argv"].index("-m") + 1], "custom-lead")
        overrides = self.config_overrides(plan["argv"])
        self.assertEqual(overrides["model_reasoning_effort"], "high")
        for role in DEFAULTS:
            content = tomllib.loads(
                plan["role_files"][overrides[f"agents.{role}.config_file"]]
            )
            self.assertEqual(content["model"], f"custom-{role}")
            self.assertEqual(content["model_reasoning_effort"], "low")

    def test_durable_paths_depend_on_role_settings_and_not_task_text(self):
        first = self.preview("--prompt", "First task")
        second = self.preview("--prompt", "Second task")
        changed = self.preview("--engineer-effort", "low")
        self.assertEqual(first["role_files"], second["role_files"])
        self.assertNotEqual(set(first["role_files"]), set(changed["role_files"]))

    def test_model_strings_round_trip_as_toml(self):
        model = 'custom-"quote"-\\path-é'
        plan = self.preview("--engineer-model", model)
        overrides = self.config_overrides(plan["argv"])
        content = tomllib.loads(
            plan["role_files"][overrides["agents.engineer.config_file"]]
        )
        self.assertEqual(content["model"], model)

    def test_invalid_inputs_fail_before_creating_files(self):
        cases = (
            ["--engineer-model", "  "],
            ["--skeptic-model", "bad\nmodel"],
            ["--architect-model", "bad\x7fmodel"],
            ["--codex", ""],
            ["--gatekeeper-effort", "quick"],
            ["--engineer-effort"],
            ["--cwd", str(self.root / "missing")],
            ["--unknown-option"],
            ["--", "-m", "override"],
            ["--", "-c", 'model="override"'],
            ["--", "resume", "--last"],
            ["--", "--sandbox"],
        )
        for args in cases:
            with self.subTest(args=args):
                self.run_launcher(*args, expected=2)
                self.assertFalse(self.state.exists())

    def test_forwarded_native_flags_and_exec_mode(self):
        plan = self.preview(
            "--exec", "--", "--sandbox", "read-only", "--json", "--skip-git-repo-check"
        )
        self.assertEqual(plan["argv"][1], "exec")
        for flag in ("--sandbox", "--json", "--skip-git-repo-check"):
            self.assertIn(flag, plan["argv"])
        interactive = self.preview(
            "--", "--ask-for-approval", "never", "--no-alt-screen"
        )
        self.assertIn("--ask-for-approval", interactive["argv"])

    def test_real_child_receives_args_and_complete_persistent_files(self):
        executable = self.fake_codex()
        args = ["--codex", executable, "--prompt", "A literal $(touch never-created)"]
        plan = self.preview(*args)
        result = json.loads(self.run_launcher(*args).stdout)
        self.assertEqual(result["argv"], plan["argv"][1:])
        for filename, content in plan["role_files"].items():
            path = Path(filename)
            self.assertEqual(path.read_text(), content)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.workspace / "never-created").exists())
        self.run_launcher(*args)  # Reusing a published team must succeed.

    def test_child_exit_code_is_preserved(self):
        self.run_launcher("--codex", self.fake_codex(exit_code=7), expected=7)

    def test_changed_existing_role_file_is_not_overwritten(self):
        args = ["--codex", self.fake_codex()]
        self.run_launcher(*args)
        path = next(self.state.rglob("engineer.toml"))
        path.write_text("unexpected contents")
        self.run_launcher(*args, expected=2)
        self.assertEqual(path.read_text(), "unexpected contents")

    def test_symlinked_role_file_is_rejected(self):
        plan = self.preview()
        path = Path(next(iter(plan["role_files"])))
        path.parent.mkdir(parents=True)
        outside = self.root / "outside.toml"
        outside.write_text("keep this")
        path.symlink_to(outside)
        self.run_launcher("--codex", self.fake_codex(), expected=2)
        self.assertEqual(outside.read_text(), "keep this")

    def test_concurrent_launches_share_complete_files(self):
        executable = self.fake_codex()
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(
                executor.map(
                    lambda _: self.run_launcher("--codex", executable), range(12)
                )
            )
        self.assertEqual(len(results), 12)
        self.assertEqual(len(list(self.state.rglob("*.toml"))), 3)


if __name__ == "__main__":
    unittest.main()
