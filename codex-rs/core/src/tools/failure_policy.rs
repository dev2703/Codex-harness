//! Deterministic, turn-local observation only. Never exposes command/error text.
use std::collections::VecDeque;

const WINDOW: usize = 20;
const THRESHOLD: usize = 3;
const MAX_IDENTITY_BYTES: usize = 16 * 1024;
const MAX_ERROR_BYTES: usize = 64 * 1024;

// Deliberately no Debug/Serialize: these values can contain credentials.
#[derive(PartialEq, Eq)]
pub(super) struct FailureFingerprint {
    tool: &'static str,
    identity: Vec<String>,
    exit_code: i32,
    error: String,
}

impl FailureFingerprint {
    pub(super) fn new(
        tool: &'static str,
        command: &[String],
        cwd: &str,
        exit_code: i32,
        error: &str,
    ) -> Option<Self> {
        // Skip oversized evidence rather than equate truncated, different errors.
        if exit_code == 0
            || command.len() > 256
            || error.len() > MAX_ERROR_BYTES
            || command.iter().map(String::len).sum::<usize>() + cwd.len() > MAX_IDENTITY_BYTES
        {
            return None;
        }
        let mut identity = command
            .iter()
            .map(|arg| normalize(arg, false))
            .collect::<Vec<_>>();
        // A different working directory can make an otherwise identical call progress.
        identity.push(cwd.to_owned());
        Some(Self {
            tool,
            identity,
            exit_code,
            error: normalize(error, true),
        })
    }
}

struct Observation {
    call_id: String,
    failure: Option<FailureFingerprint>,
}

/// Safe recent evidence. Contains no model-provided command or error text.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) struct RepeatedFailure {
    pub(super) matching_failures: usize,
    pub(super) tool: &'static str,
    pub(super) exit_code: i32,
}

pub(super) fn primary_replan_enabled(enabled: bool, is_worker: bool, is_reviewer: bool) -> bool {
    enabled && !is_worker && !is_reviewer
}

/// Delivers at most one advisory per originating turn, even across later streaks.
#[derive(Default)]
pub(super) struct ReplanNoticeState {
    pending: Option<RepeatedFailure>,
    delivered: bool,
}

impl ReplanNoticeState {
    pub(super) fn queue(&mut self, diagnostic: Option<RepeatedFailure>) {
        if !self.delivered {
            self.pending = diagnostic;
        }
    }

    pub(super) fn take(&mut self) -> Option<RepeatedFailure> {
        let notice = self.pending.take()?;
        self.delivered = true;
        Some(notice)
    }
}

#[derive(Default)]
pub(super) struct FailureObserver {
    history: VecDeque<Observation>,
}

impl FailureObserver {
    /// None is a progress/unknown boundary, never a failed operation.
    /// Returns bounded evidence (matching count), with no sensitive text.
    pub(super) fn observe(
        &mut self,
        call_id: &str,
        failure: Option<FailureFingerprint>,
    ) -> Option<RepeatedFailure> {
        if self.history.iter().any(|event| event.call_id == call_id) {
            return None;
        }
        let matching = failure.as_ref().map_or(0, |fingerprint| {
            1 + self
                .history
                .iter()
                .rev()
                .take_while(|event| event.failure.as_ref() == Some(fingerprint))
                .count()
        });
        let diagnostic = failure.as_ref().and_then(|failure| {
            (matching == THRESHOLD).then_some(RepeatedFailure {
                matching_failures: matching,
                tool: failure.tool,
                exit_code: failure.exit_code,
            })
        });
        if self.history.len() == WINDOW {
            self.history.pop_front();
        }
        self.history.push_back(Observation {
            call_id: call_id.to_owned(),
            failure,
        });
        // One diagnostic per streak, rather than one per subsequent failure.
        diagnostic
    }
}

fn normalize(text: &str, collapse_whitespace: bool) -> String {
    let mut result = String::with_capacity(text.len());
    for piece in text.split_inclusive(char::is_whitespace) {
        let token = piece.trim_end_matches(char::is_whitespace);
        let bare = token
            .trim_matches(|ch: char| matches!(ch, '\'' | '"' | '(' | ')' | '[' | ']' | ',' | ';'));
        let temporary_path = normalized_temporary_path(bare);
        // UUIDs/timestamps in argv can be meaningful resource selectors. Their
        // arbitrary values are normalized only in diagnostic error metadata.
        let replacement = if let Some(path) = temporary_path.as_deref() {
            Some(path)
        } else if collapse_whitespace && is_uuid(bare) {
            Some("<random-id>")
        } else if collapse_whitespace && is_timestamp(bare) {
            Some("<timestamp>")
        } else {
            None
        };
        if let Some(replacement) = replacement {
            let prefix_len = token.len()
                - token
                    .trim_start_matches(|ch: char| {
                        matches!(ch, '\'' | '"' | '(' | ')' | '[' | ']' | ',' | ';')
                    })
                    .len();
            result.push_str(&token[..prefix_len]);
            result.push_str(replacement);
            result.push_str(&token[prefix_len + bare.len()..]);
        } else {
            result.push_str(token);
        }
        let whitespace = &piece[token.len()..];
        if collapse_whitespace {
            if !whitespace.is_empty() && !result.ends_with(' ') {
                result.push(' ');
            }
        } else {
            // Shell whitespace can be significant inside quotes. Keep it exact.
            result.push_str(whitespace);
        }
    }
    if collapse_whitespace {
        result.trim().to_owned()
    } else {
        result
    }
}

fn normalized_temporary_path(token: &str) -> Option<String> {
    let path = token
        .strip_prefix("/tmp/")
        .or_else(|| token.strip_prefix("/var/tmp/"))?;
    let (root, suffix) = path.split_once('/').unwrap_or((path, ""));
    let generated_root = root
        .strip_prefix(".tmp")
        .or_else(|| root.strip_prefix("tmp"));
    if !is_uuid(root)
        && !generated_root
            .is_some_and(|id| id.len() >= 6 && id.bytes().all(|byte| byte.is_ascii_alphanumeric()))
    {
        return None;
    }
    // Preserve meaningful filenames beneath the generated temporary root.
    Some(if suffix.is_empty() {
        "<temporary-path>".into()
    } else {
        format!("<temporary-path>/{suffix}")
    })
}

fn is_uuid(token: &str) -> bool {
    token.len() == 36
        && token.bytes().enumerate().all(|(index, byte)| {
            if matches!(index, 8 | 13 | 18 | 23) {
                byte == b'-'
            } else {
                byte.is_ascii_hexdigit()
            }
        })
}

fn is_timestamp(token: &str) -> bool {
    let bytes = token.as_bytes();
    // ISO 8601 date/time; retain ordinary numeric test counts and error codes.
    bytes.len() >= 19
        && bytes.iter().enumerate().all(|(index, byte)| match index {
            4 | 7 => *byte == b'-',
            10 => *byte == b'T',
            13 | 16 => *byte == b':',
            _ => {
                byte.is_ascii_digit()
                    || (index >= 19 && matches!(byte, b'.' | b'Z' | b'+' | b'-' | b':'))
            }
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn failure(command: &str, error: &str) -> Option<FailureFingerprint> {
        FailureFingerprint::new("shell", &[command.to_owned()], "/project", 1, error)
    }

    #[test]
    fn repeated_failure_emits_once_at_third_completion() {
        let mut observer = FailureObserver::default();
        for index in 1..=10 {
            assert_eq!(
                observer
                    .observe(&index.to_string(), failure("cargo test", "test a failed"))
                    .map(|diagnostic| diagnostic.matching_failures),
                (index == 3).then_some(3),
            );
        }
    }

    #[test]
    fn changed_command_error_exit_tool_and_cwd_break_streak() {
        let baseline = || failure("cargo test", "test a failed");
        let variations = [
            failure("cargo test b", "test a failed"),
            failure("cargo test", "test b failed"),
            FailureFingerprint::new(
                "shell",
                &["cargo test".into()],
                "/project",
                2,
                "test a failed",
            ),
            FailureFingerprint::new(
                "exec_command",
                &["cargo test".into()],
                "/project",
                1,
                "test a failed",
            ),
            FailureFingerprint::new(
                "shell",
                &["cargo test".into()],
                "/other",
                1,
                "test a failed",
            ),
        ];
        for changed in variations {
            let mut observer = FailureObserver::default();
            assert_eq!(observer.observe("1", baseline()), None);
            assert_eq!(observer.observe("2", baseline()), None);
            assert_eq!(observer.observe("3", changed), None);
            assert_eq!(observer.observe("4", baseline()), None);
        }
    }

    #[test]
    fn successful_repeated_calls_never_flag() {
        let mut observer = FailureObserver::default();
        for index in 0..40 {
            assert_eq!(observer.observe(&index.to_string(), None), None);
        }
        assert_eq!(observer.history.len(), WINDOW);
    }

    #[test]
    fn repair_or_unknown_outcome_breaks_failure_streak() {
        let mut observer = FailureObserver::default();
        observer.observe("test-1", failure("cargo test", "assertion failed"));
        observer.observe("test-2", failure("cargo test", "assertion failed"));
        assert_eq!(observer.observe("repair", None), None);
        assert_eq!(
            observer.observe("test-3", failure("cargo test", "assertion failed")),
            None
        );
        assert_eq!(observer.observe("test-pass", None), None);
    }

    #[test]
    fn changing_test_counts_are_progress() {
        let mut observer = FailureObserver::default();
        for remaining in [3, 2, 1] {
            assert_eq!(
                observer.observe(
                    &remaining.to_string(),
                    failure("test", &format!("{remaining} tests failed"))
                ),
                None
            );
        }
    }

    #[test]
    fn flaky_success_breaks_streak() {
        let mut observer = FailureObserver::default();
        for index in 0..30 {
            let event = if index % 3 == 2 {
                None
            } else {
                failure("test", "flaky failure")
            };
            assert_eq!(observer.observe(&index.to_string(), event), None);
        }
    }

    #[test]
    fn duplicate_completion_does_not_count_twice() {
        let mut observer = FailureObserver::default();
        for _ in 0..10 {
            assert_eq!(
                observer.observe("same-call", failure("false", "failed")),
                None
            );
        }
        assert_eq!(observer.history.len(), 1);
    }

    #[test]
    fn ring_evicts_oldest_and_turns_are_isolated() {
        let mut observer = FailureObserver::default();
        observer.observe("old", failure("false", "failed"));
        for index in 0..WINDOW {
            observer.observe(&index.to_string(), None);
        }
        assert_eq!(observer.history.len(), WINDOW);
        assert!(observer.history.iter().all(|event| event.call_id != "old"));
        assert_eq!(observer.observe("old", failure("false", "failed")), None);
        let mut next_turn = FailureObserver::default();
        assert_eq!(next_turn.observe("old", failure("false", "failed")), None);
    }

    #[test]
    fn normalizes_volatile_evidence_without_erasing_error_codes() {
        let first = failure(
            "cat /tmp/.tmpABC123/input",
            "2026-11-09T12:34:56Z failed /tmp/.tmpABC123/input (550e8400-e29b-41d4-a716-446655440000) code 42",
        );
        let second = failure(
            "cat /tmp/.tmpDEF456/input",
            "2026-11-09T12:35:57.123Z  failed\n/var/tmp/.tmpDEF456/input (550e8400-e29b-41d4-a716-446655440001) code 42",
        );
        assert!(first == second);
        assert!(failure("test", "code 42") != failure("test", "code 43"));
        assert!(failure("test", "α failed") != failure("test", "β failed"));
    }

    #[test]
    fn preserves_shell_quoting_arguments_and_meaningful_paths() {
        assert!(failure("echo 'a  b'", "failed") != failure("echo 'a b'", "failed"));
        assert!(failure("cat '/tmp/run'", "failed") != failure("cat /tmp/run", "failed"));
        assert!(failure("cat src/a.rs", "failed") != failure("cat src/b.rs", "failed"));
        assert!(
            failure("test", "/tmp/.tmpABC123/a.rs failed")
                != failure("test", "/tmp/.tmpDEF456/b.rs failed")
        );
        assert!(failure("cat /tmp/a.rs", "failed") != failure("cat /tmp/b.rs", "failed"));
        let split =
            FailureFingerprint::new("shell", &["a".into(), "b".into()], "/project", 1, "failed");
        assert!(split != failure("a b", "failed"));
    }

    #[test]
    fn changed_command_identifiers_and_dates_are_not_volatile_error_metadata() {
        // Resource IDs and dates in argv select different inputs. Only error
        // metadata may discard arbitrary UUID/timestamp tokens.
        for commands in [
            [
                "fetch 550e8400-e29b-41d4-a716-446655440000",
                "fetch 550e8400-e29b-41d4-a716-446655440001",
                "fetch 550e8400-e29b-41d4-a716-446655440002",
            ],
            [
                "query 2026-11-09T12:34:56Z",
                "query 2026-11-09T12:35:57Z",
                "query 2026-11-09T12:36:58Z",
            ],
        ] {
            let mut observer = FailureObserver::default();
            for (index, command) in commands.into_iter().enumerate() {
                assert_eq!(
                    observer.observe(&index.to_string(), failure(command, "unchanged error")),
                    None
                );
            }
        }
    }

    #[test]
    fn skips_oversized_evidence_instead_of_matching_truncated_output() {
        assert!(failure("test", &"x".repeat(MAX_ERROR_BYTES + 1)).is_none());
        assert!(failure(&"x".repeat(MAX_IDENTITY_BYTES + 1), "failed").is_none());
        assert!(FailureFingerprint::new("shell", &["true".into()], "/project", 0, "").is_none());
    }
    #[test]
    fn primary_replan_notice_is_bounded_to_one_per_turn() {
        let event = || RepeatedFailure {
            matching_failures: 3,
            tool: "shell",
            exit_code: 1,
        };
        let mut current_turn = ReplanNoticeState::default();
        assert!(current_turn.take().is_none());
        current_turn.queue(Some(event()));
        current_turn.queue(Some(event()));
        assert_eq!(current_turn.take(), Some(event()));
        current_turn.queue(Some(event()));
        assert!(current_turn.take().is_none());
        let mut next_turn = ReplanNoticeState::default();
        next_turn.queue(Some(event()));
        assert_eq!(next_turn.take(), Some(event()));
    }

    #[test]
    fn progress_cancels_undelivered_replan_without_consuming_turn_budget() {
        let event = || RepeatedFailure {
            matching_failures: 3,
            tool: "shell",
            exit_code: 1,
        };
        let mut notice = ReplanNoticeState::default();
        notice.queue(Some(event()));
        notice.queue(None);
        assert!(notice.take().is_none());
        notice.queue(Some(event()));
        assert_eq!(notice.take(), Some(event()));
        notice.queue(None);
        notice.queue(Some(event()));
        assert!(notice.take().is_none());
    }


    #[test]
    fn later_relevant_completion_clears_undelivered_notice() {
        let mut notice = ReplanNoticeState::default();
        notice.queue(Some(RepeatedFailure { matching_failures: 3, tool: "shell", exit_code: 1 }));
        notice.queue(None);
        assert!(notice.take().is_none());
    }

    #[test]
    fn primary_replan_requires_opt_in_and_excludes_reviewers_and_workers() {
        for enabled in [false, true] {
            for worker in [false, true] {
                for reviewer in [false, true] {
                    assert_eq!(primary_replan_enabled(enabled, worker, reviewer), enabled && !worker && !reviewer);
                }
            }
        }
    }

}
