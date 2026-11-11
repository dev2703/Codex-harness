# Proposal: authenticated receipts and context-budget policy

This proposal covers two related evidence gaps from the audit.

## Execution receipt

A bounded receipt should include repository and contract IDs, source/HEAD hashes, selected checks, exit codes, final diff hash, verification status, and a timestamp. It must omit raw prompts, command output, secrets, and unbounded event logs.

A checksum is useful for corruption detection but is not authentication. The implementation must select and document a trust model (for example, a locally protected signing key), reject stale or replayed receipts, and keep evidence separate from authorization.

Promotion should require:

- a valid signature;
- matching repository and contract;
- matching final diff hash;
- all required checks passed;
- no unresolved verification state.

## Context budget

Add bounded per-turn telemetry for context size, compaction transitions, retained evidence pointers, and omission markers. Provide soft and hard thresholds and explicitly identify one compression owner so multiple compression layers cannot silently stack.

Acceptance tests should cover tampering, replay, stale HEAD, failed checks, threshold transitions, privacy, and fresh-session handoff. Do not claim token, cost, latency, or model-quality improvements without reproducible model-backed experiments.

This is a proposal only; it does not claim the feature is implemented.