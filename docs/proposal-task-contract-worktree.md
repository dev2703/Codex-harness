# Proposal: task contracts and isolated worktrees

This proposal turns the first missing capability in the audit into an implementation contract.

## Contract

Before an implementation session starts, persist a bounded, versioned `task_contract.json` containing:

- repository URL and starting HEAD;
- task ID, goal, owned paths, invariants, checks, and acceptance criteria;
- selected execution mode and an expiry;
- no prompts, secrets, approval grants, or model instructions.

The contract is user-visible and immutable after publication. A new contract version gets a new ID and must not silently replace an active one.

## Worktree lifecycle

1. Verify the repository and starting HEAD.
2. Create an ephemeral worktree under a caller-selected safe root.
3. Bind tool/path diagnostics to the worktree and contract ID.
4. Run checks and collect bounded evidence.
5. On success, produce a promotion candidate; on failure or interruption, remove or quarantine the worktree without touching the user's active checkout.

## Acceptance tests

- repository mismatch and stale HEAD are rejected;
- path traversal and undeclared-path writes are rejected or reported;
- concurrent task IDs receive distinct worktrees;
- killed sessions clean up or leave an explicit quarantine record;
- contract files are bounded, schema-validated, and cannot carry authority-bearing settings;
- sandbox and approval behavior remains controlled by the native runtime.

This is a proposal only; it does not claim the feature is implemented.