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

    def run_launcher(
        self, *args, expected=0, input_text=None, stdin_stream=None, launch_cwd=None
    ):
        process_stdin = stdin_stream if stdin_stream is not None else subprocess.DEVNULL
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
            input=input_text,
            stdin=process_stdin if input_text is None else None,
            cwd=launch_cwd,
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

    def role_checking_codex(self):
        if tomllib is None:
            raise unittest.SkipTest("Child role validation requires Python 3.11+")
        executable = Path(self.fake_codex())
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, pathlib, sys, tomllib\n"
            "argv = sys.argv[1:]\n"
            "overrides = {}\n"
            "for index, arg in enumerate(argv[:-1]):\n"
            "    if arg == '-c':\n"
            "        key, value = argv[index + 1].split('=', 1)\n"
            "        overrides[key] = tomllib.loads('value=' + value)['value']\n"
            "roles = {}\n"
            "for role in ('engineer', 'gatekeeper', 'skeptic'):\n"
            "    path = pathlib.Path(overrides[f'agents.{role}.config_file'])\n"
            "    assert path.is_absolute()\n"
            "    config = tomllib.loads(path.read_text(encoding='utf-8'))\n"
            "    assert config['name'] == role\n"
            "    assert config['description'] and config['developer_instructions']\n"
            "    assert not {'sandbox_mode', 'approval_policy'}.intersection(config)\n"
            "    roles[role] = config\n"
            "print(json.dumps({'argv': argv, 'roles': roles, 'overrides': overrides, "
            "'stdin': sys.stdin.read()}))\n"
        )
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

    def test_informational_task_keeps_the_team_workflow(self):
        plan = self.preview(
            "--engineer-effort", "low", "--prompt", "how does a knowledge graph work"
        )
        self.assertIn("how does a knowledge graph work", plan["prompt"])
        self.assertIn(
            "roles for both implementation and informational tasks",
            plan["root_briefing"],
        )
        overrides = self.config_overrides(plan["argv"])
        engineer = tomllib.loads(
            plan["role_files"][overrides["agents.engineer.config_file"]]
        )
        self.assertEqual(engineer["model_reasoning_effort"], "low")
        for role in DEFAULTS:
            self.assertIn(f"agents.{role}.config_file", overrides)

    def test_startup_status_leaves_stdout_machine_readable(self):
        result = self.run_launcher(
            "--codex", self.fake_codex(), "--engineer-effort", "low"
        )
        json.loads(result.stdout)
        self.assertIn("Team roles configured:", result.stderr)
        for selection in (
            "architect=gpt-6.1-sol/ultra",
            "engineer=gpt-6-sol/low",
            "gatekeeper=gpt-6-luna/low",
            "skeptic=gpt-6-astra/high",
        ):
            self.assertIn(selection, result.stderr)
        preview = self.run_launcher("--dry-run")
        self.assertEqual(preview.stderr, "")
        json.loads(preview.stdout)

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
            "--exec",
            "--prompt",
            "Inspect the repository",
            "--",
            "--sandbox",
            "read-only",
            "--json",
            "--skip-git-repo-check",
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

    @unittest.skipIf(os.name == "nt", "POSIX signal exit status")
    def test_child_signal_uses_shell_exit_status(self):
        executable = Path(self.fake_codex())
        executable.write_text(
            f"#!{sys.executable}\n"
            "import os, signal\n"
            "os.kill(os.getpid(), signal.SIGTERM)\n"
        )
        self.run_launcher("--codex", str(executable), expected=143)

    def test_prompt_file_is_relative_to_launch_directory_and_preserves_content(self):
        task = '  Inspect "quotes", $(touch never-created), café\r\n-c payload\n  '
        prompt_file = self.root / "task with spaces.txt"
        prompt_file.write_bytes(task.encode("utf-8"))
        result = self.run_launcher(
            "--dry-run", "--prompt-file", prompt_file.name, launch_cwd=self.root
        )
        plan = json.loads(result.stdout)
        self.assertTrue(plan["prompt"].endswith("User task:\n" + task))
        self.assertEqual(plan["argv"][-1], plan["prompt"])
        self.assertFalse(self.state.exists())
        self.assertFalse((self.root / "never-created").exists())
        self.assertFalse((self.workspace / "never-created").exists())

    def test_stdin_task_sources_preserve_literal_input(self):
        task = (
            '  Diagnose "quotes", $(touch never-created), café\r\n--prompt injected\n  '
        )
        for args in (
            ("--prompt-file", "-"),
            ("--prompt", "-"),
            ("--exec",),
            ("--exec", "--prompt-file", "-"),
        ):
            with self.subTest(args=args):
                result = self.run_launcher("--dry-run", *args, input_text=task)
                plan = json.loads(result.stdout)
                self.assertTrue(plan["prompt"].endswith("User task:\n" + task))
                self.assertEqual(plan["argv"][-1], plan["prompt"])
                self.assertFalse(self.state.exists())
                self.assertFalse((self.workspace / "never-created").exists())

    def test_stdin_task_reaches_child_after_roles_are_published(self):
        task = "Implement the parser\nPreserve all public methods.\n"
        result = self.run_launcher(
            "--codex", self.fake_codex(), "--exec", input_text=task
        )
        argv = json.loads(result.stdout)["argv"]
        self.assertEqual(argv[0], "exec")
        self.assertTrue(argv[-1].endswith("User task:\n" + task))
        self.assertEqual(len(list(self.state.rglob("*.toml"))), 3)

    def test_explicit_task_keeps_piped_input_for_native_runtime(self):
        executable = Path(self.fake_codex())
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "print(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read()}))\n"
        )
        context = "Additional context\n$(touch never-created)\n"
        result = self.run_launcher(
            "--codex",
            str(executable),
            "--exec",
            "--prompt",
            "Inspect this",
            input_text=context,
        )
        observed = json.loads(result.stdout)
        self.assertEqual(observed["stdin"], context)
        self.assertTrue(observed["argv"][-1].endswith("User task:\nInspect this"))

    def test_file_task_content_and_piped_context_reach_configured_child(self):
        executable = self.role_checking_codex()
        prompt_file = self.root / "task.txt"
        context = "Keep this piped context\n$(touch never-created)\n"
        for task in ("-", '\ufeffPreserve "quotes"\r\n$(touch never-created)\n'):
            with self.subTest(task=task):
                prompt_file.write_bytes(task.encode("utf-8"))
                result = self.run_launcher(
                    "--codex",
                    executable,
                    "--exec",
                    "--prompt-file",
                    str(prompt_file),
                    "--engineer-model",
                    "file-task-engineer",
                    "--engineer-effort",
                    "low",
                    "--",
                    "--sandbox",
                    "read-only",
                    "--json",
                    input_text=context,
                )
                observed = json.loads(result.stdout)
                self.assertEqual(observed["stdin"], context)
                argv = observed["argv"]
                self.assertTrue(argv[-1].endswith("User task:\n" + task))
                self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
                self.assertIn("--json", argv)
                self.assertTrue(observed["overrides"]["agents.enabled"])
                engineer = observed["roles"]["engineer"]
                self.assertEqual(engineer["model"], "file-task-engineer")
                self.assertEqual(engineer["model_reasoning_effort"], "low")
                self.assertEqual(len(observed["roles"]), 3)
                self.assertFalse((self.workspace / "never-created").exists())

    def test_dash_named_prompt_file_is_distinct_from_stdin_sentinel(self):
        executable = self.role_checking_codex()
        file_task = "Use the task from the dash-named file\n"
        (self.root / "-").write_text(file_task)
        context = "Use this distinct piped input\n"
        for file_arg, expected_task, expected_stdin in (
            ("./-", file_task, context),
            ("-", context, ""),
        ):
            with self.subTest(file_arg=file_arg):
                result = self.run_launcher(
                    "--codex",
                    executable,
                    "--exec",
                    "--prompt-file",
                    file_arg,
                    input_text=context,
                    launch_cwd=self.root,
                )
                observed = json.loads(result.stdout)
                self.assertTrue(
                    observed["argv"][-1].endswith("User task:\n" + expected_task)
                )
                self.assertEqual(observed["stdin"], expected_stdin)

    def test_blank_dash_named_prompt_file_fails_before_publication(self):
        for data in (b"", b" \t\r\n"):
            with self.subTest(data=data):
                (self.root / "-").write_bytes(data)
                result = self.run_launcher(
                    "--codex",
                    "missing-codex",
                    "--exec",
                    "--prompt-file",
                    "./-",
                    input_text="A nonempty piped context\n",
                    launch_cwd=self.root,
                    expected=2,
                )
                self.assertIn("empty or whitespace-only", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Team roles configured:", result.stderr)
                self.assertFalse(self.state.exists())

    def test_dash_file_with_directory_suffix_fails_before_publication(self):
        (self.root / "-").write_text("A task in a regular file\n")
        for source in ("-/", "-/."):
            with self.subTest(source=source):
                result = self.run_launcher(
                    "--codex",
                    "missing-codex",
                    "--exec",
                    "--prompt-file=" + source,
                    input_text="A distinct piped task\n",
                    launch_cwd=self.root,
                    expected=2,
                )
                self.assertIn(source, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Team roles configured:", result.stderr)
                self.assertFalse(self.state.exists())

    def test_invalid_task_sources_fail_before_publication_or_launch(self):
        invalid_utf8 = self.root / "invalid.txt"
        invalid_utf8.write_bytes(b"\xff")
        invalid_nul = self.root / "nul.txt"
        invalid_nul.write_bytes(b"Inspect\x00the parser")
        empty = self.root / "empty.txt"
        empty.write_text(" \t\n")
        for args in (
            ("--prompt-file", str(self.root / "missing.txt")),
            ("--prompt-file", str(self.workspace)),
            ("--prompt-file", str(invalid_utf8)),
            ("--prompt-file", str(invalid_nul)),
            ("--prompt-file", str(empty)),
            ("--prompt", ""),
            ("--prompt", " \t\n"),
            ("--prompt", "task", "--prompt-file", str(empty)),
            ("--prompt", "-"),
            ("--prompt-file", "-"),
            ("--exec",),
        ):
            with self.subTest(args=args):
                result = self.run_launcher(
                    "--codex", "missing-codex", *args, expected=2
                )
                self.assertEqual(result.stdout, "")
                self.assertFalse(self.state.exists())
                self.assertNotIn("Team roles configured:", result.stderr)

    def test_required_stdin_rejects_unusable_task_bytes(self):
        for data in (b" \t\r\n", b"\xff", b"Inspect\x00the parser"):
            with self.subTest(data=data):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(LAUNCHER),
                        "--exec",
                        "--cwd",
                        str(self.workspace),
                        "--state-dir",
                        str(self.state),
                    ],
                    input=data,
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertFalse(self.state.exists())
                self.assertNotIn(b"Team roles configured:", result.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX terminal fixture")
    def test_required_terminal_stdin_fails_without_waiting(self):
        master, slave = os.openpty()
        try:
            for args in (("--prompt", "-"), ("--prompt-file", "-"), ("--exec",)):
                with self.subTest(args=args):
                    result = self.run_launcher(*args, expected=2, stdin_stream=slave)
                    self.assertIn("requires piped stdin", result.stderr)
                    self.assertFalse(self.state.exists())
        finally:
            os.close(master)
            os.close(slave)

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

    def test_concurrent_stdin_launches_read_complete_roles(self):
        executable = self.role_checking_codex()
        tasks = [
            f"Handle task {index}\nPreserve literal $(touch never-created)\n"
            for index in range(12)
        ]
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(
                executor.map(
                    lambda task: self.run_launcher(
                        "--codex", executable, "--exec", input_text=task
                    ),
                    tasks,
                )
            )
        self.assertEqual(len(results), 12)
        self.assertEqual(len(list(self.state.rglob("*.toml"))), 3)
        for task, result in zip(tasks, results):
            observed = json.loads(result.stdout)
            self.assertTrue(observed["argv"][-1].endswith("User task:\n" + task))
            self.assertEqual(observed["stdin"], "")
            for role, (model, effort) in DEFAULTS.items():
                config = observed["roles"][role]
                self.assertEqual(config["model"], model)
                self.assertEqual(config["model_reasoning_effort"], effort)
        self.assertFalse((self.workspace / "never-created").exists())


if __name__ == "__main__":
    unittest.main()
