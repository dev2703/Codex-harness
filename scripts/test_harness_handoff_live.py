"""Reject inert command arguments as evidence that a live test actually ran."""

import importlib.util
from pathlib import Path
import shlex
import unittest

SPEC = importlib.util.spec_from_file_location(
    "harness_handoff_live", Path(__file__).with_name("harness_handoff_live.py")
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class LiveEvidenceGuards(unittest.TestCase):
    def test_only_the_actual_shell_command_can_prove_execution(self):
        command = "python3 -B -m unittest -v"
        quoted = shlex.quote(command)
        for observed in (command, f"/bin/zsh -lc {quoted}", f"/bin/sh -l -c {quoted}"):
            self.assertTrue(runner.exact_shell_command(observed, command), observed)
        for observed in (
            f"printf '%s' {quoted}",
            f"python3 -c {quoted}",
            f"/bin/sh -c true {quoted}",
            f"/bin/sh -lc {shlex.quote(command + '; true')}",
            "unclosed 'quote",
        ):
            self.assertFalse(runner.exact_shell_command(observed, command), observed)


if __name__ == "__main__":
    unittest.main()
