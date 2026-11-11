#!/usr/bin/env python3
"""Safety and source-shape checks for the Jev reference runner; no network or API keys."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("harness_jev_reference.py")
SPEC = importlib.util.spec_from_file_location("harness_jev_reference", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)

# Synthetic source is deliberately not an upstream copy. It has an unreachable
# model branch that would fail if the runner included more than the typed prefix.
SOURCE = """
COMPOUND = re.compile(r"[;&]")
CHAIN_SPLIT = re.compile(r"&&")
HARMLESS_REDIRECT = re.compile(r"2>/dev/null")
CD = re.compile(r"^cd ")
def hard_rule(command: str) -> Decision | None:
    if command == "blocked":
        return Decision("deny", "hard-rule", "fixture")
    return None
def _one(command: str) -> bool:
    return command == "safe"
def fast_path(command: str) -> Decision | None:
    if command in ("safe", "blocked"):
        return Decision("allow", "fast-path", "fixture")
    return None
def decide(command: str, tool: str = "Bash", user_message: str = "", cwd: str = "") -> Decision:
    if found := hard_rule(command):
        return found
    if found := fast_path(command):
        return found
    return ask_jev("MUST_NOT_CALL_MODEL", "MUST_NOT_READ_KEY")
"""


def fixture(command="blocked", expected="deny", function="hard_rule"):
    return {
        "reference_repository": runner.REFERENCE,
        "reference_commit": runner.REVISION,
        "requirements": {
            "network": False,
            "model_calls": 0,
            "isolated_home": True,
            "pinned_default_policy": True,
        },
        "cases": [
            {
                "id": "fixture",
                "function": function,
                "command": command,
                "expected": expected,
            }
        ],
    }


class ReferenceRunnerTests(unittest.TestCase):
    def test_deny_order_is_evaluated_even_if_fast_path_also_allows(self):
        data = fixture()
        data["cases"][0]["also_verify"] = "deterministic order"
        result = runner.evaluate(
            {
                "fixture": data,
                "source": SOURCE,
                "policy": {"hard_deny": [], "fast_path": []},
            }
        )
        self.assertEqual(result["passed"], 1)
        self.assertEqual(result["cases"][0]["ordered_source"], "hard-rule")

    def test_model_branch_is_absent_for_an_ambiguous_command(self):
        namespace = runner.deterministic_namespace(
            SOURCE, {"hard_deny": [], "fast_path": []}
        )
        self.assertIsNone(namespace["decide_prefix"]("ambiguous"))
        self.assertNotIn("ask_jev", namespace)
        self.assertNotIn("os", namespace)
        self.assertNotIn("subprocess", namespace)

    def test_reversed_source_order_is_an_error(self):
        source = SOURCE.replace(
            "if found := hard_rule(command):", "if found := fast_path(command):", 1
        )
        with self.assertRaisesRegex(ValueError, "hard rules before"):
            runner.deterministic_namespace(source, {"hard_deny": [], "fast_path": []})

    def test_command_strings_are_data_and_never_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "must-not-exist"
            data = fixture(
                command=f"touch {marker}", expected=None, function="fast_path"
            )
            result = runner.evaluate(
                {
                    "fixture": data,
                    "source": SOURCE,
                    "policy": {"hard_deny": [], "fast_path": []},
                }
            )
            self.assertEqual(result["passed"], 1)
            self.assertFalse(marker.exists())
            self.assertEqual(result["command_strings_executed"], 0)

    def test_fixture_cannot_select_another_commit_or_disable_isolation(self):
        for key, value in [
            ("reference_commit", "main"),
            ("reference_repository", "someone/else"),
            ("requirements", {"network": True}),
        ]:
            data = fixture()
            data[key] = value
            with self.assertRaises(ValueError):
                runner.validate_fixture(data)

    def test_unsupported_functions_duplicate_ids_and_oversized_commands_fail(self):
        data = fixture()
        data["cases"][0]["function"] = "ask_jev"
        with self.assertRaises(ValueError):
            runner.validate_fixture(data)
        data = fixture()
        data["cases"].append(copy.deepcopy(data["cases"][0]))
        with self.assertRaises(ValueError):
            runner.validate_fixture(data)
        data = fixture(command="x" * (runner.MAX_COMMAND + 1))
        with self.assertRaises(ValueError):
            runner.validate_fixture(data)

    def test_local_git_replace_cannot_change_the_pinned_source(self):
        import subprocess
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory)

            def git(*arguments):
                return subprocess.check_output(
                    [
                        "git",
                        "-c",
                        "user.name=Fixture",
                        "-c",
                        "user.email=fixture@example.invalid",
                        "-c",
                        "commit.gpgsign=false",
                        "-C",
                        directory,
                        *arguments,
                    ],
                    text=True,
                ).strip()

            git("init", "--quiet")
            path = reference / "jev_gate.py"
            path.write_text("original source\n")
            git("add", "jev_gate.py")
            git("commit", "-qm", "original")
            original = git("rev-parse", "HEAD")
            path.write_text("replacement source\n")
            git("commit", "-qam", "replacement")
            replacement = git("rev-parse", "HEAD")
            git("replace", original, replacement)
            self.assertEqual(
                git("show", f"{original}:jev_gate.py"), "replacement source"
            )
            with patch.object(runner, "REVISION", original):
                self.assertEqual(
                    runner.committed_file(reference, "jev_gate.py"), "original source\n"
                )

    def test_missing_promisor_blob_cannot_launch_upload_pack(self):
        import os
        import subprocess
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference"
            reference.mkdir()
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": directory,
                "GIT_CONFIG_NOSYSTEM": "1",
            }

            def git(*arguments):
                return subprocess.check_output(
                    [
                        "git",
                        "-c",
                        "user.name=Fixture",
                        "-c",
                        "user.email=fixture@example.invalid",
                        "-c",
                        "commit.gpgsign=false",
                        "-C",
                        str(reference),
                        *arguments,
                    ],
                    text=True,
                    env=env,
                ).strip()

            git("init", "--quiet")
            (reference / "jev_gate.py").write_text("original source\n")
            git("add", "jev_gate.py")
            git("commit", "-qm", "original")
            revision = git("rev-parse", "HEAD")
            blob = git("rev-parse", "HEAD:jev_gate.py")
            marker = root / "fetch-attempted"
            upload_pack = root / "upload-pack"
            upload_pack.write_text(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('attempted')\n"
                "raise SystemExit(1)\n"
            )
            upload_pack.chmod(0o700)
            git("config", "extensions.partialClone", "origin")
            git("config", "remote.origin.promisor", "true")
            git("config", "remote.origin.partialclonefilter", "blob:none")
            git("config", "remote.origin.url", str(reference))
            git("config", "remote.origin.uploadpack", str(upload_pack))
            git("config", "protocol.file.allow", "always")
            (reference / ".git/objects" / blob[:2] / blob[2:]).unlink()

            # Positive control: the incomplete local repository really would
            # launch its configured uploader, even for this fixed git show.
            control = subprocess.run(
                ["git", "-C", str(reference), "show", f"{revision}:jev_gate.py"],
                capture_output=True,
                env=env,
                timeout=30,
            )
            self.assertNotEqual(control.returncode, 0)
            self.assertTrue(marker.exists(), control.stderr.decode())
            marker.unlink()
            with patch.object(runner, "REVISION", revision):
                with self.assertRaises(subprocess.CalledProcessError):
                    runner.committed_file(reference, "jev_gate.py")
            self.assertFalse(marker.exists())

            # Git 2.43 ignores NO_LAZY_FETCH. Removing it reproduces that
            # behavior while still testing the real transport-level fallback.
            real_run = subprocess.run

            def older_git(*arguments, **kwargs):
                kwargs["env"] = dict(kwargs["env"])
                kwargs["env"].pop("GIT_NO_LAZY_FETCH", None)
                return real_run(*arguments, **kwargs)

            with patch.object(runner, "REVISION", revision):
                with patch.object(runner.subprocess, "run", older_git):
                    with self.assertRaises(subprocess.CalledProcessError):
                        runner.committed_file(reference, "jev_gate.py")
            self.assertFalse(marker.exists())

    def test_worker_refuses_the_current_network_namespace(self):
        import os

        if not Path("/proc/self/ns/net").exists():
            self.skipTest("requires Linux network namespaces")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "payload.json"
            path.write_text(
                json.dumps({"outer_netns": os.readlink("/proc/self/ns/net")})
            )
            with self.assertRaisesRegex(RuntimeError, "distinct loopback-only"):
                runner.worker(path)


if __name__ == "__main__":
    unittest.main()
