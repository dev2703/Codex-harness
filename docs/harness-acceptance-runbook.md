# Integrated harness acceptance

The integrated branch preserves the failure-observer, durable-handoff, role-model,
Jev-reference, output-evidence and primary-replanning commits, including schema
correction `9ac73cb8e36a565c85d15b70b5d3cfa34ae7c89f`. Replanning and strict role
validation remain disabled by default. Keep PR #1 draft until the native and
live-model feature gates pass.

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
placement. They do not verify a real model's diagnosis, repair quality, approval
behavior or read-only tool enforcement. The live scenarios in
[the original acceptance gate](harness-handoff-acceptance.md) remain required.
JSON evidence includes source revision, CLI version/hash, commands and outcomes.
CI retains the reports and build logs as artifacts, including on failures.

## Observed verification on 2026-11-09

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
