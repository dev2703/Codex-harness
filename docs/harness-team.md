# Configurable engineering team

Run `python3 scripts/harness_team.py` to start an engineering team using native
Codex custom roles. Python 3.10+ and a Codex executable supporting custom roles
are required. Use `--codex` to select the binary built from this repository.

| Role                       | Default model | Default effort | Assignment                                                                                          |
| -------------------------- | ------------- | -------------- | --------------------------------------------------------------------------------------------------- |
| Lead architect and arbiter | `gpt-6.1-sol` | `ultra`        | Creates task contracts, reviews architectural invariants, settles disagreements and merge conflicts |
| Autonomous engineer        | `gpt-6-sol`   | `medium`       | Writes production code, modifies modules, runs shell commands and checks                            |
| Gatekeeper                 | `gpt-6-luna`  | `low`          | Reviews tool parameters and paths, runs relevant cheap linters                                      |
| Adversarial skeptic        | `gpt-6-astra` | `high`         | Attacks diffs, fuzzes boundary cases, searches for races                                            |

Every model and effort has a CLI flag. These defaults are a starting preset;
model availability and supported effort depend on the selected native client,
provider, and account. Ultra uses the native client's effort handling.

Preview the command and complete role files without creating files or launching
Codex:

```sh
python3 scripts/harness_team.py --dry-run \
  --prompt "Implement the next task in this repository"
```

Launch with explicit assignments and the normal native sandbox flag:

```sh
python3 scripts/harness_team.py \
  --codex ./codex-rs/target/debug/codex \
  --architect-model gpt-6.1-sol --architect-effort ultra \
  --engineer-model gpt-6-sol --engineer-effort medium \
  --gatekeeper-model gpt-6-luna --gatekeeper-effort low \
  --skeptic-model gpt-6-astra --skeptic-effort high \
  --prompt "Implement the next task in this repository" \
  -- --sandbox workspace-write
```

Use `--engineer-effort low` for the lighter engineer configuration. Other
recognized effort values are `none`, `minimal`, `high`, `xhigh`, `max`, and
`persistent`; the native runtime determines which values the model supports.
Model identifiers receive syntax validation in the launcher. Catalog and backend
checks follow the native client's configuration, including its optional
`strict_role_model_validation` feature.

Use `--cwd /path/to/project` to target another project. Without `--prompt`, the
interactive lead acknowledges its configuration and waits for a task. For a
non-interactive task:

```sh
python3 scripts/harness_team.py --exec \
  --prompt "Fix the failing parser test and verify the change" \
  -- --sandbox workspace-write --json
```

Arguments following `--` are forwarded as individual process arguments. Supported
shared flags are `--sandbox`, `--profile`, `--add-dir`, `--image`,
`--local-provider`, `--oss`, `--approve-for-me`,
`--dangerously-bypass-approvals-and-sandbox`, `--strict-config`, and `--help`.
Interactive mode also supports `--ask-for-approval`/`-a`, `--no-alt-screen`,
`--search`, and `--no-daemon`. Exec mode also supports `--json`, `--ephemeral`,
`--skip-git-repo-check`, `--color`, `--output-schema`, and
`--output-last-message`/`-o`. Forwarded flags with values use separate arguments,
as in `--sandbox read-only`. Routing flags, arbitrary config overrides, and
subcommands are rejected; use the launcher's role flags to select models and
efforts. Run the launcher again for a new task; native resume/fork subcommands
are outside this launcher interface.

The lead receives a workflow requesting a contract with task ID, goal, owned
paths, scope, architectural invariants, checks, acceptance criteria, and required
evidence. For implementation tasks it requests gatekeeper preflight review,
engineer implementation, skeptic review, and architect arbitration. This
workflow is a model instruction, subject to higher-priority session policy;
the launcher does not implement a deterministic scheduler or intercept every
tool call. Informational tasks can stay with the lead.

The lead is the root session; `engineer`, `gatekeeper`, and `skeptic` are custom
agent types. The initial user prompt carries the lead workflow, preserving root
developer instructions. Children receive native custom-role developer
instructions. Authentication, provider settings, sandbox, and approvals use the
normal native configuration. The launcher enables multi-agent tools, including
native tools when the V2 backend is selected, without forcing a backend.

Gatekeeper recommendations are advisory. Native tool validation, execution policy,
sandbox, and approval review enforce execution boundaries. Sub-second gatekeeper
latency is a target requiring measurement, not a guaranteed property of a model
or effort flag.

Role files are created before starting Codex, under
`<cwd>/.codex-harness/teams/<content-hash>/` by default. Use `--state-dir` to
choose another persistent location. Role paths are absolute, files are published
atomically, and matching configurations reuse their files. Conflicting contents
and symlinks below the selected state root fail without overwriting the target.
Keep these files for sessions that reference them. The launcher leaves
`CODEX_HOME` and existing config files untouched, and uses an argument array
without a shell. Native subprocess exit codes are returned to the caller.

Offline verification:

```sh
python3 -B -m unittest discover -s scripts -p 'test_harness_team.py' -v
```

Tests exercise argument routing, TOML round trips, invalid inputs, publication,
concurrent launches, and child process execution using a local fixture. Native
`--help` smoke checks verify both command forms parse. Live model delegation,
review effectiveness, and latency have not been measured.

The role mechanism follows the official
[custom agent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents).
