//! Adapter for existing completion events. No execution or approval decisions.
use super::failure_policy::FailureFingerprint;
use super::failure_policy::FailureObserver;
use super::failure_policy::ReplanNoticeState;
use super::failure_policy::primary_replan_enabled;
use crate::session::turn_context::TurnContext;
use codex_protocol::items::CommandExecutionStatus;
use codex_protocol::items::TurnItem;
use codex_protocol::protocol::ExecCommandSource;
use std::sync::Mutex;

#[derive(Default)]
struct FailureObservationState {
    observer: FailureObserver,
    replan: ReplanNoticeState,
}

fn replan_enabled(turn: &TurnContext) -> bool {
    primary_replan_enabled(
        turn.config.harness_replan_after_repeated_failure,
        turn.parent_thread_id.is_some()
            || matches!(turn.session_source, codex_protocol::protocol::SessionSource::SubAgent(_)),
        crate::guardian::is_basic_session_source(&turn.session_source),
    )
}

pub(crate) fn observe_completed(turn: &TurnContext, item: &TurnItem) {
    let Some((call_id, failure)) = completed_observation(item) else {
        return;
    };
    let state = turn
        .extension_data
        .get_or_init(|| Mutex::new(FailureObservationState::default()));
    let Ok(mut state) = state.lock() else {
        return;
    };
    let diagnostic = state.observer.observe(&call_id, failure);
    if replan_enabled(turn) {
        // A later completion conservatively invalidates an undelivered notice.
        // Update under the observer lock so a concurrent progress event cannot
        // be overtaken by an earlier diagnostic's pending-state update.
        state.replan.queue(diagnostic);
    }
    drop(state);
    if let Some(diagnostic) = diagnostic {
        tracing::debug!(
            target: "codex_core::harness",
            event = "harness.repeated_failure",
            reason = "consecutive_matching_command_failures",
            mode = "observe_only",
            matching_failures = diagnostic.matching_failures,
            tool_family = diagnostic.tool,
            exit_code = diagnostic.exit_code,
            window_capacity = 20,
            "Repeated failed command observed"
        );
    }
}

/// Consumed immediately before recording into the primary's next normal input.
pub(crate) fn take_replan_notice(turn: &TurnContext) -> Option<String> {
    if !replan_enabled(turn) {
        return None;
    }
    let state = turn.extension_data.get::<Mutex<FailureObservationState>>()?;
    let diagnostic = state.lock().ok()?.replan.take()?;
    Some(format!(
        "The harness observed {} matching failed {} command completions (exit {}). Assess whether these were parallel, intentional retries, or an actual non-progress loop before deciding what to do. If progress has stalled, reconsider the command inputs, failure evidence and repair plan rather than blindly repeating the same operation. This diagnostic does not authorize tools, escalation, extra agents or additional model calls. Existing approvals and sandbox boundaries still apply.",
        diagnostic.matching_failures, diagnostic.tool, diagnostic.exit_code
    ))
}

fn completed_observation(item: &TurnItem) -> Option<(String, Option<FailureFingerprint>)> {
    Some(match item {
        TurnItem::CommandExecution(command) => {
            let tool = match command.source {
                ExecCommandSource::Agent => "shell",
                ExecCommandSource::UnifiedExecStartup => "exec_command",
                // User commands and process interactions are conservative boundaries.
                _ => "other_command",
            };
            let failure =
                if tool != "other_command" && command.status == CommandExecutionStatus::Failed {
                    command.exit_code.and_then(|exit_code| {
                        command.aggregated_output.as_deref().and_then(|output| {
                            FailureFingerprint::new(
                                tool,
                                &command.command,
                                &command.cwd.to_string(),
                                exit_code,
                                output,
                            )
                        })
                    })
                } else {
                    None
                };
            (command.id.clone(), failure)
        }
        // Even failed patches can have committed partial changes. Do not infer
        // lack of progress across them or across tools whose outcome is unknown.
        TurnItem::FileChange(_)
        | TurnItem::DynamicToolCall(_)
        | TurnItem::McpToolCall(_)
        | TurnItem::CollabAgentToolCall(_)
        | TurnItem::SubAgentActivity(_)
        | TurnItem::FunctionCallOutput(_)
        | TurnItem::HookPrompt(_)
        | TurnItem::UserMessage(_)
        | TurnItem::ImageView(_)
        | TurnItem::WebSearch(_)
        | TurnItem::ImageGeneration(_)
        | TurnItem::Extension(_)
        | TurnItem::ContextCompaction(_)
        | TurnItem::EnteredReviewMode(_)
        | TurnItem::ExitedReviewMode(_) => (item.id(), None),
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use codex_protocol::items::CommandExecutionItem;
    use serde_json::json;

    fn command(
        status: CommandExecutionStatus,
        exit_code: Option<i32>,
        source: ExecCommandSource,
    ) -> TurnItem {
        let mut command: CommandExecutionItem = serde_json::from_value(json!({
            "id": "command", "command": ["sh", "-c", "false"],
            "cwd": "file:///project", "parsed_cmd": [], "source": "agent",
            "status": "failed", "aggregated_output": "fixture error"
        }))
        .expect("valid command fixture");
        command.status = status;
        command.exit_code = exit_code;
        command.source = source;
        TurnItem::CommandExecution(command)
    }

    #[test]
    fn completed_command_status_and_exit_code_both_required() {
        for (status, code, expected) in [
            (CommandExecutionStatus::Failed, Some(1), true),
            (CommandExecutionStatus::Failed, None, false),
            (CommandExecutionStatus::Failed, Some(0), false),
            (CommandExecutionStatus::Completed, Some(0), false),
            (CommandExecutionStatus::Declined, Some(-1), false),
            (CommandExecutionStatus::InProgress, None, false),
        ] {
            let (_, failure) =
                completed_observation(&command(status, code, ExecCommandSource::Agent))
                    .expect("command observed");
            assert_eq!(failure.is_some(), expected);
        }
    }

    #[test]
    fn missing_output_is_unknown_but_observed_empty_output_is_a_failure() {
        let mut item = command(
            CommandExecutionStatus::Failed,
            Some(1),
            ExecCommandSource::Agent,
        );
        let TurnItem::CommandExecution(command) = &mut item else {
            unreachable!("command fixture");
        };
        command.aggregated_output = None;
        assert!(
            completed_observation(&item)
                .expect("command observed")
                .1
                .is_none()
        );
        let TurnItem::CommandExecution(command) = &mut item else {
            unreachable!("command fixture");
        };
        command.aggregated_output = Some(String::new());
        assert!(
            completed_observation(&item)
                .expect("command observed")
                .1
                .is_some()
        );
    }

    #[test]
    fn tool_evidence_and_compaction_break_command_failure_streaks() {
        use codex_protocol::items::ContextCompactionItem;
        use codex_protocol::items::ImageViewItem;

        let image: ImageViewItem = serde_json::from_value(json!({
            "id": "view", "path": "file:///project/failure.png"
        }))
        .expect("image fixture");
        for boundary in [
            TurnItem::ImageView(image),
            TurnItem::ContextCompaction(ContextCompactionItem {
                id: "compact".into(),
            }),
        ] {
            let mut observer = FailureObserver::default();
            let observe_failure = |observer: &mut FailureObserver, id: &str| {
                observer.observe(
                    id,
                    FailureFingerprint::new(
                        "shell",
                        &["false".into()],
                        "file:///project",
                        1,
                        "unchanged failure",
                    ),
                )
            };
            assert_eq!(observe_failure(&mut observer, "1"), None);
            assert_eq!(observe_failure(&mut observer, "2"), None);
            let (id, failure) = completed_observation(&boundary).expect("progress boundary");
            assert_eq!(observer.observe(&id, failure), None);
            assert_eq!(observe_failure(&mut observer, "3"), None);
        }
    }

    #[test]
    fn unified_terminal_failure_counts_but_interactions_and_user_calls_do_not() {
        for (source, expected) in [
            (ExecCommandSource::Agent, true),
            (ExecCommandSource::UnifiedExecStartup, true),
            (ExecCommandSource::UnifiedExecInteraction, false),
            (ExecCommandSource::UserShell, false),
        ] {
            let (_, failure) =
                completed_observation(&command(CommandExecutionStatus::Failed, Some(1), source))
                    .expect("command observed");
            assert_eq!(failure.is_some(), expected);
        }
    }
}
