# Proposal: managed hook policy

The repository already has hook and sandbox infrastructure, but a production harness needs an explicit policy layer for which hooks may run and what they may change.

## Proposed behavior

- Define a versioned hook manifest with event, executable identity, allowed paths, timeout, output bound, and failure mode.
- Support an administrator-managed-only mode that ignores untrusted project/session hook additions.
- Run hooks with inherited or narrower permissions; never widen the parent sandbox or approval policy.
- Record hook decisions and exit status in bounded diagnostics.
- Reject stale manifests and hooks that mutate undeclared paths.

## Acceptance

Test missing, malformed, slow, failing, reordered, and unauthorized hooks. Verify a denied hook cannot become a successful tool result and that hook output is redacted/bounded.

This proposal preserves native approval and sandbox ownership.