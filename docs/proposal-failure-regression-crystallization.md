# Proposal: crystallize verified failures into regressions

The current failure observer detects repeated failures but intentionally stops at diagnosis. A Claude-style self-healing harness can turn reviewed failures into durable safeguards without automatically changing behavior.

## Proposed workflow

1. Capture a bounded failure fingerprint and evidence references, excluding secrets, raw output, and command text.
2. Require human or maintainer confirmation before writing a regression artifact.
3. Generate a minimal deterministic fixture and expected classification.
4. Add the fixture to the pinned offline regression corpus.
5. Re-run the new fixture in CI and link it from the receipt/audit record.

## Safety boundaries

No automatic source edits, prompt injection, rollback, approval changes, or model escalation. Unknown or flaky failures remain triaged evidence, not regressions.

## Acceptance

Cover privacy redaction, deduplication, flaky classification, fixture bounds, review provenance, and rejection of authority-bearing payloads.