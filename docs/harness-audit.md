# Harness audit (initial)

## Baseline

- Repository: `dev2703/Codex-harness`, fork of `openai/codex`.
- Base branch: `main`.
- Base commit: `25f4940d4affcf8c60f96ffec5e3752e1f590b8a`.
- Audit branch: `feat/harness-policy-v1`.
- Local build/tests have not run in this GitHub-only session; Cargo is unavailable.

## Existing mechanisms inspected

- `codex-rs/core/src/agent/child_config.rs` prepares child configuration using captured step settings and requested overrides. It references shared runtime policy for spawn/reload and uses existing role configuration.
- `codex-rs/core/src/agent/role.rs` applies bounded role overrides to an existing session config. Its module documentation states roles may customize children or reduce capabilities but cannot replace parent authority.
- `codex-rs/core/src/compact.rs` uses pre/post compact hooks and compaction summary/context structures. This is an existing integration point for later context work.
- `codex-rs/core/src/config/mod.rs` loads agent roles and contains configuration/feature wiring.
- `codex-rs/core/Cargo.toml` is the `codex-core` package manifest.

## Initial assessment

The repository already has role-based child configuration, hook runtime integration, and compaction hooks. The first feature should integrate with existing tool-event plumbing and remain observe-only. The inspected anchors alone do not establish the exact event insertion point or whether an equivalent repeated-failure detector exists; trace the tool execution/event flow and search for existing retry/loop diagnostics before implementing.

## Baseline experiment status

No deterministic replays have been run. Normal command success, repeated identical failure, and test failure followed by repair remain to be measured in an executable environment. Do not infer model-call/token outcomes from source inspection.
