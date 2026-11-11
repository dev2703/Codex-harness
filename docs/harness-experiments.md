# V1 observe-only experiment record

Date: 2026-11-09. Upstream baseline: `25f4940d4affcf8c60f96ffec5e3752e1f590b8a`.
Policy/replay revision: `c9d34ccfe8f555419fb779d85c962432bb34b362`.
The existing fork audit commit `db92906d5` is preserved in branch history.

## Implemented boundary

The existing completed-item path calls a diagnostic adapter. State is lazily
attached to the originating turn's existing `ExtensionData`. History contains at
most 20 command or progress observations; the third consecutive equal failure
emits one debug log with `event=harness.repeated_failure`,
`reason=consecutive_matching_command_failures`, `mode=observe_only`, a count,
tool family and exit status as safe recent evidence.
Enable it using `RUST_LOG=codex_core::harness=debug` and the normal Codex log setup.
No command, output, call identifier or fingerprint is emitted by this log.

Only failed terminal shell/unified-exec command items with nonzero exit codes
are fingerprinted. Success, denial, unknown outcomes, user commands, interactions,
patch completion (including possible partial repair), MCP, dynamic tools and
collaboration-tool completion are conservative streak boundaries. Duplicate
completion IDs retained in the window are ignored. Tool outcomes, approval and
sandbox control, prompts, model selection, retries and compaction are unchanged.

Fingerprints compare tool family, argv, cwd, exit code and normalized evidence.
Argv boundaries, shell whitespace/quotes, error codes, test counts and filenames
are retained. Recognized ISO timestamps, UUIDs and generated temporary roots
(`tmp`/`.tmp` plus at least six alphanumeric characters, or a UUID, under `/tmp`
or `/var/tmp`) are normalized; paths beneath those roots retain their suffix.
Unrecognized formats remain exact. Commands above 256 arguments or 16 KiB of
identity, and errors above 64 KiB, become unknown boundaries rather than matches
on truncated evidence. Sensitive normalized text stays in bounded turn memory;
the fingerprint intentionally has no Debug or serialization implementation.

## Completed checks

These commands run from the repository root with the pinned Rust 1.95.0 tools on
PATH. They need no model credentials or Cargo dependency resolution:

```sh
rustc --edition 2024 --test scripts/harness-replay.rs -o /tmp/harness-policy-tests
/tmp/harness-policy-tests
rustc --edition 2024 scripts/harness-replay.rs -o /tmp/harness-replay
/tmp/harness-replay
clippy-driver --edition 2024 --test scripts/harness-replay.rs -D warnings -o /tmp/harness-policy-clippy
rustfmt --edition 2024 --check --config skip_children=true codex-rs/core/src/tools/failure_policy.rs codex-rs/core/src/tools/failure_observation.rs codex-rs/core/src/tools/mod.rs codex-rs/core/src/session/mod.rs scripts/harness-replay.rs
git diff --check origin/main HEAD
```

Results: 11 policy unit tests passed, 0 failed; standalone policy Clippy passed
with warnings denied; changed-source formatting and diff whitespace checks passed.
Stable rustfmt warned that `imports_granularity=Item` is a nightly-only option.
The adapter's two additional unit tests require a complete codex-core test build;
they are not included in the standalone count above.

Actual model-free replay output:

```jsonl
{"scenario":"successful_command","commands":3,"diagnostics":0,"pass":true}
{"scenario":"repeated_failure","commands":3,"diagnostics":1,"pass":true}
{"scenario":"test_failure_then_repair","commands":3,"diagnostics":0,"pass":true}
{"scope":"model_free_policy_replay","elapsed_ms":33,"model_calls":0,"model_tokens":null}
```

The replays execute real fixture commands and assert exit codes and policy
decisions. The third case creates failing file content, runs an assertion,
repairs the file through a successful command, and reruns the assertion. These
are policy experiments, not end-to-end stock-Codex versus fork comparisons.
No model tokens, cost, task accuracy or latency gains are established. The 33 ms
is one local fixture run, not a performance claim.

## Cargo verification

The stock checkout was kept separate from the implementation worktree. Both
`cargo build` and a focused stock test were launched before source edits:

```sh
cd codex-rs
cargo build
cargo test -p codex-core --lib tools::events::tests::net_zero_patch_emits_empty_turn_diff -- --exact
```

Dependency fetching completed (including V8 and Chromium submodules). The
focused stock test acquired the shared artifact lock and compiled dependencies;
the stock `cargo build` waited for that lock. Neither reached a passing result.
`cargo install --locked just dotslash cargo-nextest` also began compiling.

All three were interrupted with SIGINT to avoid exhausting the approximately
8 GB workspace filesystem. Their observed exit statuses were **130**, not test
failures or passes. At the final check the filesystem used 7.0 GB (94%), with
461 MB available. The caches alone included approximately 3.5 GB of Git
dependencies and 1.4 GB of registry dependencies. No source compilation error
was established by this interruption.

Local logs retained for continuation:

- `/tmp/harness-baseline-build.log`
- `/tmp/harness-baseline-test.log`
- `/tmp/harness-helpers.log`

The baseline worktree remains `/workspace/Codex-harness`; the feature worktree
is `/workspace/harness-work`. The helper installation did not complete, so
`just fmt`, `just fix -p codex-core`, and `just test -p codex-core` have not passed.
Standalone Clippy/formatting are limited substitutes, not full-workspace checks.

**Phase 1 ship gate is not satisfied.** Continue in an environment with more
build storage: first complete stock build/focused test, install the documented
helpers, then run feature `just fmt`, `just fix -p codex-core`, and
`just test -p codex-core`. Run the pinned model-backed baseline/fork scenarios
after the integrated binary builds. Keep the draft PR unmerged meanwhile.

## Limits and remaining gate

The adapter is not a universal observer for every tool implementation. It cannot
infer unobserved filesystem edits, causality among parallel failures, or whether
three identical failures are intentional/flaky retries. Different remote
executors using identical cwd/argv are not independently identified by this
completion schema. Exact matching on unrecognized volatile formats can miss
loops; recognized placeholders can still merge distinct evidence. Thresholds
are provisional constants, not a new configuration surface.

Keep the PR draft until stock build/test, full codex-core tests, repository
formatter/helpers and Cargo Clippy are verified. Then run model-backed pinned
baseline/fork experiments with identical settings, collecting all-agent usage
only when telemetry is actually available. Do not activate escalation or later
phases based on these model-free results.
