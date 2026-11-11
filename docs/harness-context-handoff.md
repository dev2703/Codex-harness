# Explicit durable context handoff

Status: implementation prepared; build, formatting, unit tests and real sandbox acceptance are pending verification. Keep the feature draft until the gate below passes.

This adds native CLI commands for a portable, explicit packet of user context. It reuses the normal `codex exec` startup path. Full thread history, compaction and native resume remain the existing mechanisms; a packet is a condensed handoff, not a native-session snapshot or automatic memory.

## Usage

Write explicit notes:

```json
{
  "goal": "Repair the failing parser test while preserving the public API",
  "constraints": ["Preserve the public API"],
  "decisions": ["Reuse the existing parser"],
  "completed": ["Located the relevant implementation"],
  "pending": ["Reproduce the failure", "Repair and rerun the tests"],
  "verification": [
    {
      "command": "cargo test -p parser",
      "status": "unverified",
      "evidence": "Not run in this session"
    }
  ]
}
```

Then save, inspect, and continue:

```sh
codex handoff save --notes notes.json --output handoff.json
codex handoff inspect handoff.json
codex handoff continue handoff.json --sandbox workspace-write "Continue the pending repair"
```

Use `codex -C /path/to/project handoff save ...` or `handoff continue ... -C /path/to/project` to choose the project. Relative file arguments resolve against that selected directory. Saving records its Git HEAD and dirty state when available. A changed/missing HEAD on continuation fails before a model session starts unless explicitly acknowledged with `--allow-repository-change`.

Continue starts a fresh native exec session. Current CLI/config model and sandbox choices apply; the packet cannot restore model/provider choices, approvals, hook trust, tools or write grants. It renders the notes as one user message, with a reminder to recheck unverified evidence and unfinished work. Nested resume/fork/review commands are rejected; use native `codex exec resume` for existing full history.

## Format and durability

- Strict versioned JSON with typed goal, constraints, decisions, completed/pending work and verification statuses.
- Verification status is explicitly `passed`, `failed` or `unverified`; the codec does not execute cited commands or promote claims to success.
- Notes are bounded to 32 KiB and packets to 64 KiB. Unknown fields, corrupt checksums, unsupported versions and non-regular inputs fail.
- SHA-256 detects corruption; it is not authentication or encryption. Keep sensitive packets in an appropriate local location.
- Same-directory temporary publication uses file sync, no-clobber persistence and Unix directory sync. Existing files/symlinks are never replaced. Unix packet mode is 0600.
- Unix final-component symlink replacement is prevented with O_NOFOLLOW. Windows symlink rejection is best effort and is not a reparse-point security boundary.
- No extra model calls are made while saving or inspecting. Continue uses the user-selected/default native model with no hardcoded model names.

## Required verification

```sh
cd codex-rs
cargo test -p codex-cli --lib handoff
cargo test -p codex-cli --bin codex handoff_cmd
cargo build -p codex-cli --bin codex
```

Run repository formatting and Cargo Clippy, then exercise the actual built CLI in fresh sandbox projects:

1. Save a packet, exit, remove the notes input, and inspect/load from a new process.
2. Verify concurrent publishers and overwrite refusal leave one complete original packet.
3. Reject corrupt/future/oversized/symlink packets before any model call.
4. Reject mismatched Git HEAD; acknowledge it explicitly without changing current sandbox choices.
5. Have a real model reproduce a Python fixture failure and save a handoff without repairing it.
6. Start a second independent real session using only the packet and current request. Verify it repairs the fixture, passes actual tests, and preserves the public API, sentinel and original packet.
7. Start a separate continuation with the read-only sandbox. Observe an attempted fixture write fail, with no file created and no escalation/bypass.

The native CLI build, seven codec tests, four command-preparation tests, formatting,
Clippy, schema generation and offline process acceptance pass in Linux CI. The
fixed native read-only tool case also passes. A real stock-CLI baseline using
the production codec passes diagnosis and fresh-session repair; the fork's actual
live command adapter remains unverified. Use `scripts/harness_handoff_live.py`
for that gate. See [the current evidence](harness-acceptance-runbook.md) and the
shared acceptance plan in `harness-handoff-acceptance.md`.
