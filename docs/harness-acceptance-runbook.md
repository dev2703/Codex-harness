# Integrated harness acceptance

The integrated branch preserves the failure-observer, durable-handoff, role-model,
Jev-reference, output-evidence and primary-replanning commits, including schema
correction `9ac73cb8e36a565c85d15b70b5d3cfa34ae7c89f`. Replanning and strict role
validation remain disabled by default. Keep PR #1 draft until the native and
live-model feature gates pass.

## Passing native Linux gate

[CI run 42610729199](https://github.com/dev2703/Codex-harness/actions/runs/42610729199)
passes at Rust/source revision `f43a2346916a728bfe00bf48fb7028825286af56`:

- Native CLI build; 97 focused Rust tests (7 codec, 4 command preparation,
  16 failure policy, 4 observation, 15 output context, 4 role validation,
  47 feature tests); library and binary Clippy with warnings denied.
- Whole-workspace Rust formatting, 16 Linux Python guards, three real-command
  policy replays, and exact-byte generated configuration schema verification.
- All eight offline Bubblewrap acceptance groups, including separate native
  save/inspect/continue processes, six concurrent publishers, killed publishers,
  corrupt/unsupported/symlink/FIFO/oversized/authority-bearing input rejection,
  permissions, repository mismatch/acknowledgement, distinct fresh sessions,
  and a real native read-only shell write denial.
- All 11 pinned Jev cases and nine Jev guard tests pass within isolation;
  fixture command strings executed: zero; real model calls: zero.

The CLI reports `codex-cli 0.0.0`; its SHA-256 is
`48834967ec525e7defc35bcd8ef702ffd3a2a446237f449c9f7e7bb126a1b80d`.
The read-only shell starts, then returns exit 2 with `Read-only file system`;
the attempted file is absent. The namespace has only loopback, host credentials
are not mounted, and only fixture storage is writable on the host.

[The evidence artifact](https://github.com/dev2703/Codex-harness/actions/runs/42610729199/artifacts/13670953841)
contains native build/test logs, the acceptance log and JSON process evidence.
A reviewed local copy is `/private/tmp/harness-verified-evidence/`.
The earlier run failed only at a large-enum lint; the boxed continuation
arguments fix is included in the passing revision. Subsequent live-helper and
documentation commits do not change the tested Rust source or offline runner.
The final local Python guard count is 17, with one Linux-only test skipped on
macOS.

## Reproducible Linux gate

`.github/workflows/harness-policy.yml` runs on relevant pull requests, pushes to
`feat/harness-policy-v1*`, and manual dispatch. It installs the repository-pinned
Rust toolchain, enables user namespaces on a disposable Ubuntu runner, verifies
prebuilt V8 checksums, and builds the actual `codex-cli` binary. It runs focused
CLI/core/features tests and Clippy, regenerates the configuration schema and
compares its exact bytes, then executes offline native acceptance. A separate
job checks Rust formatting, Python guards and production-policy replays.

Local equivalents, from the repository root:

```sh
python3 scripts/harness_ci.py --phase checks --report-dir /tmp/harness-checks
python3 scripts/harness_ci.py --phase native --report-dir /tmp/harness-evidence
python3 scripts/harness_ci.py --phase prepare-reference \
  --reference /tmp/jev-reference-new --report-dir /tmp/harness-evidence
python3 scripts/harness_ci.py --phase acceptance \
  --codex "$PWD/codex-rs/target/debug/codex" \
  --reference /tmp/jev-reference-new --report-dir /tmp/harness-evidence
```

Dependency downloads and the pinned Jev fetch happen before offline acceptance.
The reference is read using its exact committed objects with replacement objects
disabled. The offline runner requires Linux Bubblewrap, clears inherited
credentials and configuration, checks a different loopback-only network
namespace, and gives the CLI only temporary fixture storage to write. Missing
Bubblewrap, a missing binary, isolation failure or an acceptance assertion exits
nonzero. There is no unisolated fallback.

The native continuation checks use a local mock Responses server inside the
isolated namespace. They verify process wiring, fresh sessions and user-context
placement. A fixed tool response also asks the native CLI to attempt a harmless
fixture write under read-only policy with approvals set to `never`; the test
requires observable shell startup, a permission denial and an absent output file.
These cases do not verify a real model's diagnosis, repair quality or behavior.
The live scenarios in
[the original acceptance gate](harness-handoff-acceptance.md) remain required.
JSON evidence includes source revision, CLI version/hash, commands and outcomes.
CI retains the reports and build logs as artifacts, including on failures.

## Local host observations on 2026-11-09

This continuation runs on an Apple Silicon macOS host. The earlier managed
Linux environment is not connected through the available tools. Docker is
installed but its daemon is not running, and Bubblewrap is absent.

With Rust 1.95.0 installed in temporary storage:

- Whole-workspace `cargo fmt -- --config imports_granularity=Item --check`
  passes. Stable rustfmt warns that the existing imports option needs nightly.
- All 16 production failure-policy tests pass, and three actual-command policy
  replays pass. Standalone policy Clippy passes with warnings denied.
- All seven production handoff codec/filesystem tests pass through a temporary
  Cargo crate importing the production source directly. Its Clippy passes with
  warnings denied. This is not a native CLI build.
- Jev guard tests pass with the Linux namespace test explicitly skipped on
  macOS. The real Jev runner refuses to run without Linux Bubblewrap.
- The native offline build exits 101 because the pinned `crossterm` Git
  dependency is absent from this host's cache. Native focused Rust tests and
  generated-schema verification have not passed here. JSON/schema inspection
  confirms that the merge retains the schema correction, but cannot replace
  running the generator.

A real no-tool authentication probe **passes** using the existing ChatGPT login,
stock `codex-cli 0.165.0` and the user's configured `gpt-6.1-sol` model. The process
exits 0 after returning `HARNESS_AUTH_OK`, with no tool-call events. The recorded
elapsed time is 5.12 seconds; it is not a performance comparison. The local raw
probe log is `/private/tmp/harness-live-auth-probe.json` (mode 0600).

```sh
codex -a never exec --ignore-user-config --ignore-rules --ephemeral \
  --sandbox read-only --skip-git-repo-check --json --model gpt-6.1-sol \
  -c 'model_reasoning_effort="low"' \
  'Reply with exactly HARNESS_AUTH_OK. Do not call any tools.'
```

This establishes current authentication, superseding the earlier HTTP 401
observation. It does not validate the integrated fork's live handoff behavior.
No model-quality, cost or token-saving claim follows from these checks.

## Live baseline with the production packet codec

Two real `gpt-6.1-sol` sessions also pass a stock-CLI baseline on this host.
The first runs in native read-only mode and reproduces two Python unittest
failures caused by `return a + b + 1`. Its typed notes preserve failed and
unverified evidence. A small temporary Rust driver imports the production
`handoff.rs` directly, saves the packet, removes the original notes input, and
renders the production continuation prompt. The second independent stock-CLI
session runs in workspace-write mode, rechecks the failures, changes the function
to `return a + b`, and reruns both tests successfully. Approvals are `never`.

Independent assertions pass, and the test source, `add(a, b)` signature, sentinel
and original packet remain unchanged. The session IDs differ. The observed
session durations are 20.58 and 15.99 seconds; these are not benchmark results.
The stock CLI binary SHA-256 is
`a45099154d7fb4a5ed8a56f40966a95deb938e508bab23e6985035c709e9a85a`.

Local evidence: `/private/tmp/harness-live-baseline-h_b3dnm5/report.json` in a
mode-0700 directory; the report is mode 0600. It includes exact commands, native
tool outputs, usage and thread IDs. The temporary driver is
`/private/tmp/handoff-codec-review/src/main.rs`, and the orchestration script is
`/private/tmp/harness_live_baseline.py`.

This is a live baseline using stock CLI plus the production packet codec. It
does not exercise the fork's new `handoff` command adapter, primary replanning,
role validation or read-only write-denial behavior. Those gates remain separate.

## Live fork command gate

With a built fork CLI and an existing native OpenAI login, run:

```sh
python3 scripts/harness_handoff_live.py \
  --codex /absolute/path/to/built/codex --model gpt-6.1-sol \
  --workspace "$PWD" --report /tmp/harness-live-evidence.json --timeout 180
```

The workspace parent must be outside native temporary-directory and Codex-home
write exceptions so the denied-write test is meaningful. An explicit model is
required because the runner ignores user configuration and rules. It reuses
native authentication without reading or copying credential files itself.
The runner retains a fresh fixture and private evidence directory for review.

Three real sessions must diagnose actual failures, repair from the actual
`handoff save`/`inspect`/`continue` packet, and demonstrate a native read-only
write denial. It requires distinct thread IDs, exact executed test commands,
failed and unverified notes, immutable tests/API/sentinel/packet, independent
post-repair assertions, and an observable shell-start marker before denial.
Timeouts, malformed evidence or a generic tool-startup failure do not pass.

The supplied stock CLI was rejected before fixture creation or model sessions;
local report: `/private/tmp/harness-fork-live-attempt.json`. The helper's syntax,
formatting, refusal path and command-evidence guard pass. Full execution against
the built fork remains unverified until that binary is available in an
authenticated native environment. The Linux offline CI job receives no login.
