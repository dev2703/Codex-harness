# Configurable engineering team

Run `codex team` to start an engineering team using native Codex custom roles:

```sh
codex team \
  --architect-model gpt-6.1-sol \
  --architect-effort ultra \
  --prompt "your task"
```

The `team` subcommand is implemented in this repository's native CLI. Build it
before using the new command; an existing installed release does not acquire
the command from changes to this checkout:

```sh
cd codex-rs
cargo build --locked -p codex-cli --bin codex
./target/debug/codex team --help
./target/debug/codex team --prompt "your task"
```

If `CARGO_TARGET_DIR` is set, use the binary in that directory instead. Native
`codex team` does not require Python. The original
`python3 scripts/harness_team.py` interface remains available with Python 3.10+
and a Codex executable supporting custom roles; `--codex` selects that executable.

Run the launcher directly at a terminal shell prompt. Starting ordinary `codex`
does not enable this team; use the `team` subcommand. A launch prints
`Team roles configured:` and the four model/effort
pairs to stderr before opening Codex. `--dry-run` remains JSON-only.

| Role                       | Default model | Default effort | Assignment                                                                                          |
| -------------------------- | ------------- | -------------- | --------------------------------------------------------------------------------------------------- |
| Lead architect and arbiter | `gpt-6.1-sol` | `ultra`        | Creates task contracts, reviews architectural invariants, settles disagreements and merge conflicts |
| Autonomous engineer        | `gpt-6-sol`   | `medium`       | Implements code or drafts informational answers                                                     |
| Gatekeeper                 | `gpt-6-luna`  | `low`          | Reviews tool parameters and paths, runs cheap checks, checks answer consistency                     |
| Adversarial skeptic        | `gpt-6-astra` | `high`         | Attacks diffs, boundary cases, races, and unsupported claims                                        |

Every model and effort has a CLI flag. These defaults are a starting preset;
model availability and supported effort depend on the selected native client,
provider, and account. Ultra uses the native client's effort handling.

Preview the command and complete role files without creating files or launching
Codex:

```sh
codex team --dry-run \
  --prompt "Implement the next task in this repository"
```

Launch with explicit assignments and the normal native sandbox flag:

```sh
codex team \
  --architect-model gpt-6.1-sol --architect-effort ultra \
  --engineer-model gpt-6-sol --engineer-effort medium \
  --gatekeeper-model gpt-6-luna --gatekeeper-effort low \
  --skeptic-model gpt-6-astra --skeptic-effort high \
  --prompt "Implement the next task in this repository" \
  --sandbox workspace-write
```

Use `--engineer-effort low` for the lighter engineer configuration. Other
recognized effort values are `none`, `minimal`, `high`, `xhigh`, `max`, and
`persistent`; the native runtime determines which values the model supports.
Model identifiers receive syntax validation in the launcher. Catalog and backend
checks follow the native client's configuration, including its optional
`strict_role_model_validation` feature.

Informational questions use the team too. For example:

```sh
codex team \
  --engineer-effort low \
  --prompt "how does a knowledge graph work"
```

The engineer drafts an explanation, the gatekeeper checks consistency, the
skeptic challenges assumptions, and the architect combines their findings. A
conceptual question does not require shell commands. Native agent activity
appears while the work runs.

Use `--cwd /path/to/project` to target another project. Without `--prompt`, the
interactive lead acknowledges its configuration and waits for a task. For a
non-interactive task:

```sh
codex team --exec \
  --prompt "Fix the failing parser test and verify the change" \
  --sandbox workspace-write --json
```

Native `codex team` accepts normal interactive Codex flags directly, including
`--sandbox`/`-s`, `--profile`/`-p`, `--image`/`-i`, `--add-dir`, `--worktree`,
`--ask-for-approval`/`-a`, and `--strict-config`. Both `--sandbox=read-only` and
`--sandbox read-only` work. Use `--cd`/`-C` or its `--cwd` alias to choose the
project. A positional task is also accepted: `codex team "your task"`.
`--model`/`-m` selects the architect and conflicts with an explicit
`--architect-model`. Role settings take precedence over generic `-c` routing
overrides. Root-level runtime flags work before `team` too; flags after `team`
take precedence. Profiles, authentication, provider settings, sandbox and
approval controls follow the existing native runtime.

With `--exec`, native team supports `--json`, `--ephemeral`,
`--skip-git-repo-check`, `--color`, `--output-schema`, and
`--output-last-message`/`-o`. Approval and search settings carry into exec as
configuration overrides. `--no-alt-screen` and `--no-daemon` require interactive
mode. Remote servers are rejected before role files are created because the
generated role paths refer to the local filesystem. Native `resume`, `fork`,
`review`, and other Codex commands continue to use their existing entry points;
they are not nested under `team`.

For the original Python launcher, arguments following `--` are forwarded as
individual process arguments. Supported
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
tool call. Informational tasks request the same three roles with answer-focused
assignments. With no task supplied, the lead waits without spawning agents.

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
Keep these files for sessions that reference them. Both interfaces leave
`CODEX_HOME` and existing config files untouched. The native subcommand calls
the normal interactive or exec runtime in process. The Python launcher uses an
argument array without a shell and returns native subprocess exit codes.

Offline verification:

```sh
python3 -B -m unittest discover -s scripts -p 'test_harness_team.py' -v
```

Tests exercise argument routing, TOML round trips, invalid inputs, publication,
concurrent launches, startup output separation, and child process execution using
a local fixture. Native `--help` smoke checks verify both command forms parse.

Run the native team unit and CLI integration tests from `codex-rs`:

```sh
cargo test --locked -p codex-cli --bin codex team
cargo test --locked -p codex-cli --test team_cli
```

The native tests cover normal Codex flag parsing and precedence, help and shell
completion discovery, interactive and exec configuration, atomic publication,
concurrent launches, invalid inputs, and remote rejection. Process tests use
`--dry-run` and make no model requests. A separate process smoke check is also
available (Python 3.11+):

```sh
python3 scripts/harness_team_smoke.py --codex ./codex-rs/target/debug/codex
```

A live check on 2026-11-11 used installed `codex-cli 0.165.0` with the question
above in the interactive TUI, a temporary project, and the native read-only
sandbox. Session records confirmed these model/effort settings and three
completed child sessions:

| Session    | Model         | Effort  |
| ---------- | ------------- | ------- |
| Architect  | `gpt-6.1-sol` | `ultra` |
| Engineer   | `gpt-6-sol`   | `low`   |
| Gatekeeper | `gpt-6-luna`  | `low`   |
| Skeptic    | `gpt-6-astra` | `high`  |

The architect produced the final explanation, the native CLI exited zero, and
the fixture project stayed empty. The parent session ID is
`01a1c77d-99f2-7bd0-af80-65c2a4e7f219`. An additional non-interactive marker test
verified all three roles with the default engineer effort. These are smoke tests
of routing and completion; review effectiveness and latency targets remain
unmeasured.

The role mechanism follows the official
[custom agent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents).
