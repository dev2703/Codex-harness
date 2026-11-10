"""Exercise native team smoke evidence guards with launcher-generated fixtures."""

import copy
from pathlib import Path
import tempfile
import unittest

import harness_team
import harness_team_smoke as smoke


@unittest.skipIf(smoke.tomllib is None, "TOML validation requires Python 3.11+")
class TeamSmokeGuards(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="team-smoke-guards-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.workspace = root / "workspace with spaces"
        self.workspace.mkdir()
        self.state = root / "state"
        config = harness_team.TeamConfig(
            codex="codex", cwd=self.workspace, state_dir=self.state,
            architect_model="gpt-6.1-sol", architect_effort="ultra",
            engineer_model="gpt-6-sol", engineer_effort="medium",
            gatekeeper_model="gpt-6-luna", gatekeeper_effort="low",
            skeptic_model="gpt-6-astra", skeptic_effort="high",
            prompt=smoke.TASK,
        )
        self.plan = harness_team.build_plan(config)

    def validate(self, plan=None, *, exec_mode=False):
        return smoke.validate_preview(
            self.plan if plan is None else plan,
            workspace=self.workspace, state=self.state,
            exec_mode=exec_mode, roles=smoke.ROLES,
        )

    def test_complete_fixture_passes_without_creating_state(self):
        self.assertEqual(self.validate(), self.plan["argv"])
        self.assertFalse(self.state.exists())

    def test_changed_task_and_wrong_execution_mode_are_rejected(self):
        changed = copy.deepcopy(self.plan)
        changed["prompt"] += " changed"
        changed["argv"][-1] = changed["prompt"]
        with self.assertRaisesRegex(RuntimeError, "literal task"):
            self.validate(changed)
        with self.assertRaisesRegex(RuntimeError, "interactive/exec"):
            self.validate(exec_mode=True)

    def test_incomplete_or_misrouted_roles_cannot_pass(self):
        changed = copy.deepcopy(self.plan)
        changed["role_files"].pop(next(iter(changed["role_files"])))
        with self.assertRaisesRegex(RuntimeError, "incomplete role"):
            self.validate(changed)
        changed = copy.deepcopy(self.plan)
        path = next(iter(changed["role_files"]))
        changed["role_files"][path] = changed["role_files"][path].replace(
            'model_reasoning_effort = "medium"', 'model_reasoning_effort = "low"'
        )
        with self.assertRaisesRegex(RuntimeError, "engineer effort"):
            self.validate(changed)

    def test_permission_overrides_and_published_state_are_rejected(self):
        changed = copy.deepcopy(self.plan)
        path = next(iter(changed["role_files"]))
        changed["role_files"][path] += 'sandbox_mode = "danger-full-access"\n'
        with self.assertRaisesRegex(RuntimeError, "inherited permissions"):
            self.validate(changed)
        self.state.mkdir()
        with self.assertRaisesRegex(RuntimeError, "created the state"):
            self.validate()

    def test_role_paths_cannot_escape_the_selected_state(self):
        changed = copy.deepcopy(self.plan)
        path = next(iter(changed["role_files"]))
        changed["role_files"][str(self.workspace / Path(path).name)] = changed["role_files"].pop(path)
        with self.assertRaisesRegex(RuntimeError, "escaped state"):
            self.validate(changed)


if __name__ == "__main__":
    unittest.main()
