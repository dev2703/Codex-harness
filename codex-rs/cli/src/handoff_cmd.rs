use clap::Parser;
use codex_cli::handoff;
use codex_exec::Cli as ExecCli;
use codex_utils_cli::SharedCliOptions;
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;

#[derive(Debug, Parser)]
pub(crate) struct HandoffCli {
    #[command(subcommand)]
    action: HandoffAction,
}

#[derive(Debug, clap::Subcommand)]
enum HandoffAction {
    /// Save explicit notes as an atomic packet. Makes no model calls.
    Save {
        #[arg(long)]
        notes: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        source_session: Option<String>,
    },
    /// Verify integrity and display provenance/counts without printing notes.
    Inspect { packet: PathBuf },
    /// Start a fresh exec session with user context and current permissions.
    Continue {
        packet: PathBuf,
        /// Acknowledge a different Git HEAD than the saved one.
        #[arg(long)]
        allow_repository_change: bool,
        #[command(flatten)]
        exec: ExecCli,
    },
}

impl HandoffCli {
    pub(crate) fn is_continue(&self) -> bool {
        matches!(&self.action, HandoffAction::Continue { .. })
    }

    pub(crate) fn worktree_requested(&self) -> bool {
        match &self.action {
            HandoffAction::Continue { exec, .. } => exec.shared.worktree,
            _ => false,
        }
    }
}

fn repository_head(cwd: &Path) -> Option<String> {
    let output = Command::new("git")
        .args(["rev-parse", "--verify", "HEAD"])
        .current_dir(cwd)
        .output()
        .ok()?;
    output
        .status
        .success()
        .then(|| String::from_utf8_lossy(&output.stdout).trim().to_owned())
}

fn repository_dirty(cwd: &Path) -> anyhow::Result<bool> {
    let output = Command::new("git")
        .args(["status", "--porcelain", "--untracked-files=normal"])
        .current_dir(cwd)
        .output()?;
    if !output.status.success() {
        anyhow::bail!("could not inspect repository state");
    }
    Ok(!output.stdout.is_empty())
}

fn relative_to(cwd: &Path, path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_owned()
    } else {
        cwd.join(path)
    }
}

pub(crate) fn prepare(cli: HandoffCli, root: &SharedCliOptions) -> anyhow::Result<Option<ExecCli>> {
    match cli.action {
        HandoffAction::Save {
            notes,
            output,
            source_session,
        } => {
            let cwd = root.cwd.clone().unwrap_or(std::env::current_dir()?);
            let repository_head = repository_head(&cwd);
            let repository_dirty = if repository_head.is_some() {
                Some(repository_dirty(&cwd)?)
            } else {
                None
            };
            let output = relative_to(&cwd, &output);
            handoff::save(
                &output,
                handoff::HandoffPayload {
                    notes: handoff::read_notes(&relative_to(&cwd, &notes))?,
                    source_session,
                    repository_head,
                    repository_dirty,
                },
            )?;
            println!("{}", serde_json::json!({"status":"saved", "path":output}));
            Ok(None)
        }
        HandoffAction::Inspect { packet } => {
            let cwd = root.cwd.clone().unwrap_or(std::env::current_dir()?);
            let payload = handoff::load(&relative_to(&cwd, &packet))?;
            println!(
                "{}",
                serde_json::json!({
                    "status":"valid", "version":1,
                    "source_session":payload.source_session,
                    "repository_head":payload.repository_head,
                    "repository_dirty":payload.repository_dirty,
                    "completed_count":payload.notes.completed.len(),
                    "pending_count":payload.notes.pending.len(),
                    "verification_count":payload.notes.verification.len()
                })
            );
            Ok(None)
        }
        HandoffAction::Continue {
            packet,
            allow_repository_change,
            mut exec,
        } => {
            if exec.command.is_some() {
                anyhow::bail!(
                    "handoff continue starts a fresh session; resume, fork and review subcommands are not supported"
                );
            }
            exec.shared.inherit_exec_root_options(root);
            let cwd = exec.shared.cwd.clone().unwrap_or(std::env::current_dir()?);
            let payload = handoff::load(&relative_to(&cwd, &packet))?;
            if !allow_repository_change
                && payload.repository_head.is_some()
                && payload.repository_head != repository_head(&cwd)
            {
                anyhow::bail!(
                    "repository HEAD differs from the handoff; inspect the workspace or explicitly use --allow-repository-change"
                );
            }
            exec.prompt = Some(handoff::continuation_prompt(
                &payload,
                exec.prompt.as_deref(),
            )?);
            Ok(Some(exec))
        }
    }
}

#[cfg(test)]
#[path = "handoff_cmd_tests.rs"]
mod tests;
