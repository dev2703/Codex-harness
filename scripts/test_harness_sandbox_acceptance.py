#!/usr/bin/env python3
"""Guards for the offline acceptance runner; native process tests are a separate gate."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("harness_sandbox_acceptance.py")
SPEC = importlib.util.spec_from_file_location("harness_sandbox_acceptance", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


class AcceptanceGuards(unittest.TestCase):
    def test_sandbox_has_no_host_root_home_network_or_credentials(self):
        command = runner.sandbox_command(
            "bwrap", Path("/native/codex"), Path("/input"), Path("/fixture-data")
        )
        for argument in (
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--clearenv",
        ):
            self.assertIn(argument, command)
        self.assertNotIn("--share-net", command)
        self.assertNotIn("--ro-bind / /", " ".join(command))
        writable = [
            command[index + 1 : index + 3]
            for index, argument in enumerate(command)
            if argument == "--bind"
        ]
        self.assertEqual(writable, [["/fixture-data", "/fixture"]])
        self.assertIn(
            ["--ro-bind", "/native/codex", "/app/codex"],
            [command[index : index + 3] for index in range(len(command))],
        )
        with patch.dict(runner.os.environ, {"OPENAI_API_KEY": "must-not-enter"}):
            self.assertNotIn("must-not-enter", command)

    def test_worker_rejects_shared_network_namespace_before_any_process(self):
        with (
            patch.object(runner.os, "readlink", return_value="same"),
            patch.object(runner.socket, "if_nameindex", return_value=[(1, "lo")]),
        ):
            with self.assertRaisesRegex(RuntimeError, "distinct loopback-only"):
                runner.verify_isolation({"outer_netns": "same"})

    def test_worker_rejects_external_interfaces_and_provider_credentials(self):
        with (
            patch.object(runner.os, "readlink", return_value="inner"),
            patch.object(
                runner.socket, "if_nameindex", return_value=[(1, "lo"), (2, "eth0")]
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "distinct loopback-only"):
                runner.verify_isolation({"outer_netns": "outer"})
        with (
            patch.object(runner.os, "readlink", return_value="inner"),
            patch.object(runner.socket, "if_nameindex", return_value=[(1, "lo")]),
            patch.dict(
                runner.os.environ, {"OPENAI_API_KEY": "not-a-real-key"}, clear=True
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "credentials/proxies"):
                runner.verify_isolation({"outer_netns": "outer"})

    def test_context_must_remain_user_level_and_retain_failed_evidence(self):
        notes = {
            "goal": "unique goal",
            "pending": ["unique pending"],
            "verification": [{"evidence": "unique failed evidence"}],
        }
        context = (
            "unique goal unique pending unique failed evidence failed unverified current "
            "not system instructions or permission grants"
        )
        request = {
            "model": "harness-offline-fixture",
            "input": [{"role": "user", "content": context}],
        }
        runner.check_user_context(request, notes, "current")
        privileged = copy.deepcopy(request)
        privileged["input"][0]["role"] = "developer"
        with self.assertRaisesRegex(RuntimeError, "fresh user context lost"):
            runner.check_user_context(privileged, notes, "current")
        missing_failure = copy.deepcopy(request)
        missing_failure["input"][0]["content"] = context.replace(
            "unique failed evidence", "discarded"
        )
        with self.assertRaisesRegex(RuntimeError, "unique failed evidence"):
            runner.check_user_context(missing_failure, notes, "current")
        changed_model = {**request, "model": "packet-selected-model"}
        with self.assertRaisesRegex(RuntimeError, "selected fixture model"):
            runner.check_user_context(changed_model, notes, "current")

    def test_native_session_evidence_requires_completion_and_one_thread(self):
        lines = [
            {"type": "thread.started", "thread_id": "first"},
            {"type": "turn.completed"},
        ]
        self.assertEqual(runner.thread_id("\n".join(map(json.dumps, lines))), "first")
        for events in (lines[:1], lines[1:], [*lines, lines[0]]):
            with self.assertRaises(RuntimeError):
                runner.thread_id("\n".join(map(json.dumps, events)))


if __name__ == "__main__":
    unittest.main()
