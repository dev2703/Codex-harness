//! Native entry point for the configurable engineering team.

use anyhow::Context;
use clap::Parser;
use codex_exec::Cli as ExecCli;
use codex_tui::Cli as TuiCli;
use codex_utils_cli::ApprovalModeCliArg;
use serde::Serialize;
use sha2::Digest;
use sha2::Sha256;
use std::collections::BTreeMap;
use std::io::IsTerminal;
use std::io::Read;
use std::io::Write;
use std::path::Path;
use std::path::PathBuf;

const EFFORTS: [&str; 9] = [
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
    "persistent",
];

#[derive(Debug, Parser)]
#[command(defer(configure_args))]
pub(crate) struct TeamCli {
    #[command(flatten)]
    pub interactive: TuiCli,

    /// Task for the lead architect (also positional); use - to read stdin.
    #[arg(long = "prompt", conflicts_with = "prompt", value_name = "TEXT")]
    pub task_prompt: Option<String>,

    /// Read a UTF-8 task file, or use - to read the task from stdin.
    #[arg(long, conflicts_with_all = ["task_prompt", "prompt"], value_name = "FILE")]
    pub prompt_file: Option<PathBuf>,

    /// Model for the lead architect and final arbiter (defaults to --model or gpt-6.1-sol).
    #[arg(long)]
    pub architect_model: Option<String>,
    #[arg(long, default_value = "ultra", value_parser = EFFORTS)]
    pub architect_effort: String,
    #[arg(long, default_value = "gpt-6-sol")]
    pub engineer_model: String,
    #[arg(long, default_value = "medium", value_parser = EFFORTS)]
    pub engineer_effort: String,
    #[arg(long, default_value = "gpt-6-luna")]
    pub gatekeeper_model: String,
    #[arg(long, default_value = "low", value_parser = EFFORTS)]
    pub gatekeeper_effort: String,
    #[arg(long, default_value = "gpt-6-astra")]
    pub skeptic_model: String,
    #[arg(long, default_value = "high", value_parser = EFFORTS)]
    pub skeptic_effort: String,

    /// Run non-interactively; read the task from stdin if no task is given.
    #[arg(long = "exec", conflicts_with_all = ["no_alt_screen", "no_daemon"])]
    pub exec_mode: bool,
    /// Print a JSON launch preview without creating files or making model requests.
    #[arg(long)]
    pub dry_run: bool,
    /// Persistent role config directory (defaults to <cwd>/.codex-harness/teams).
    #[arg(long, value_name = "DIR")]
    pub state_dir: Option<PathBuf>,

    /// Print native exec events as JSONL.
    #[arg(long, requires = "exec_mode")]
    pub json: bool,
    /// Run without persisting session files (role configs remain persistent).
    #[arg(long, requires = "exec_mode")]
    pub ephemeral: bool,
    /// Allow running the team outside a Git repository.
    #[arg(long, requires = "exec_mode")]
    pub skip_git_repo_check: bool,
    #[arg(long, requires = "exec_mode", value_name = "FILE")]
    pub output_schema: Option<PathBuf>,
    #[arg(long, short = 'o', requires = "exec_mode", value_name = "FILE")]
    pub output_last_message: Option<PathBuf>,
    #[arg(long, requires = "exec_mode", value_parser = ["always", "never", "auto"])]
    pub color: Option<String>,
}

fn configure_args(command: clap::Command) -> clap::Command {
    command
        .mut_arg("cwd", |arg| arg.visible_alias("cwd"))
        .mut_arg("model", |arg| {
            arg.help("Alias for --architect-model; cannot be combined with --architect-model")
        })
        .mut_arg("architect_model", |arg| arg.conflicts_with("model"))
}

pub(crate) enum TeamSession {
    Interactive(TuiCli),
    Exec(ExecCli),
}

pub(crate) struct TeamLaunch {
    pub session: TeamSession,
    pub dry_run: bool,
    pub plan: TeamPlan,
}

#[derive(Serialize)]
pub(crate) struct TeamPlan {
    pub argv: Vec<String>,
    pub role_files: BTreeMap<PathBuf, String>,
    pub root_briefing: &'static str,
    pub prompt: String,
    #[serde(skip)]
    status: String,
}

impl TeamCli {
    pub(crate) fn worktree_requested(&self) -> bool {
        self.interactive.shared.worktree
    }

    /// Root configuration overrides must already be attached to `root`.
    /// Team routing overrides are appended last, preserving native permissions.
    pub(crate) fn prepare(self, root: TuiCli) -> anyhow::Result<TeamLaunch> {
        let stdin = std::io::stdin();
        self.prepare_with_input(root, &mut stdin.lock(), stdin.is_terminal())
    }

    fn prepare_with_input(
        self,
        mut root: TuiCli,
        input: &mut impl Read,
        stdin_is_terminal: bool,
    ) -> anyhow::Result<TeamLaunch> {
        if (self.task_prompt.is_some() || self.prompt_file.is_some())
            && (self.interactive.prompt.is_some() || root.prompt.is_some())
        {
            anyhow::bail!(
                "provide the task using only one of --prompt, --prompt-file, or a positional PROMPT"
            );
        }
        let root_images = root.shared.images.clone();
        let child_images = self.interactive.shared.images.clone();
        let architect_model = self
            .interactive
            .shared
            .model
            .clone()
            .or(self.architect_model)
            .or_else(|| root.shared.model.clone())
            .unwrap_or_else(|| "gpt-6.1-sol".to_string());
        for (name, model) in [
            ("architect", &architect_model),
            ("engineer", &self.engineer_model),
            ("gatekeeper", &self.gatekeeper_model),
            ("skeptic", &self.skeptic_model),
        ] {
            validate_model(model, name)?;
        }
        crate::merge_interactive_cli_flags(&mut root, self.interactive);
        if self.exec_mode {
            if root.no_alt_screen || root.no_daemon {
                anyhow::bail!("--no-alt-screen and --no-daemon require interactive team mode");
            }
            root.shared.images = root_images;
            root.shared.images.extend(child_images);
        }
        if let Some(task) = self.task_prompt {
            root.prompt = Some(task);
        }
        let cwd = root
            .shared
            .cwd
            .as_deref()
            .map(expand_home)
            .transpose()?
            .unwrap_or(std::env::current_dir()?);
        let cwd = cwd
            .canonicalize()
            .context("could not resolve team working directory")?;
        if !cwd.is_dir() {
            anyhow::bail!(
                "team working directory is not a directory: {}",
                cwd.display()
            );
        }
        let task = read_task(
            self.prompt_file.as_deref(),
            root.prompt.take(),
            self.exec_mode,
            input,
            stdin_is_terminal,
        )?;
        let state_dir = self
            .state_dir
            .as_deref()
            .map(expand_home)
            .transpose()?
            .unwrap_or_else(|| cwd.join(".codex-harness/teams"));
        let state_dir = if state_dir.is_absolute() {
            state_dir
        } else {
            cwd.join(state_dir)
        };
        let roles = [
            (
                "engineer",
                self.engineer_model.as_str(),
                self.engineer_effort.as_str(),
                ENGINEER_DESCRIPTION,
                ENGINEER_INSTRUCTIONS,
            ),
            (
                "gatekeeper",
                self.gatekeeper_model.as_str(),
                self.gatekeeper_effort.as_str(),
                GATEKEEPER_DESCRIPTION,
                GATEKEEPER_INSTRUCTIONS,
            ),
            (
                "skeptic",
                self.skeptic_model.as_str(),
                self.skeptic_effort.as_str(),
                SKEPTIC_DESCRIPTION,
                SKEPTIC_INSTRUCTIONS,
            ),
        ];
        let mut contents = BTreeMap::new();
        for (name, model, effort, description, instructions) in roles {
            contents.insert(
                name,
                role_text(name, description, instructions, model, effort)?,
            );
        }
        let digest = format!("{:x}", Sha256::digest(serde_json::to_vec(&contents)?));
        let role_dir = state_dir.join(&digest[..20]);
        let mut role_files = BTreeMap::new();
        let mut generated = vec![
            format!(
                "model_reasoning_effort={}",
                toml_string(&self.architect_effort)?
            ),
            "features.multi_agent=true".to_string(),
            "agents.enabled=true".to_string(),
            "features.multi_agent_v2.disable_native_tools=false".to_string(),
        ];
        for (name, _, _, description, _) in roles {
            let path = role_dir.join(format!("{name}.toml"));
            generated.push(format!(
                "agents.{name}.description={}",
                toml_string(description)?
            ));
            generated.push(format!(
                "agents.{name}.config_file={}",
                toml_string(&path.to_string_lossy())?
            ));
            role_files.insert(path, contents.remove(name).context("missing team role")?);
        }
        root.shared.cwd = Some(cwd);
        root.shared.model = Some(architect_model.clone());
        let prompt = match task {
            Some(task) => format!("{ROOT_BRIEFING}\n\nUser task:\n{task}"),
            None => format!(
                "{ROOT_BRIEFING}\n\nNo task supplied yet. Acknowledge the team configuration and wait for the user's task."
            ),
        };
        root.prompt = Some(prompt.clone());
        root.config_overrides.raw_overrides.extend(generated);
        let mut argv = vec!["codex".to_string()];
        if self.exec_mode {
            argv.push("exec".to_string());
        }
        append_shared_argv(&mut argv, &root)?;
        let session = if self.exec_mode {
            let mut exec_args = vec!["codex".to_string()];
            if let Some(color) = self.color.as_ref() {
                exec_args.extend(["--color".to_string(), color.clone()]);
                argv.extend(["--color".to_string(), color.clone()]);
            }
            let mut exec = ExecCli::try_parse_from(exec_args)?;
            *exec.shared = root.shared.into_inner();
            exec.config_overrides = root.config_overrides;
            exec.strict_config = root.strict_config;
            exec.prompt = Some(prompt.clone());
            exec.json = self.json;
            exec.ephemeral = self.ephemeral;
            exec.skip_git_repo_check = self.skip_git_repo_check;
            exec.output_schema = self.output_schema;
            exec.last_message_file = self.output_last_message;
            if let Some(approval) = root.approval_policy {
                let value = approval_name(approval);
                exec.config_overrides
                    .raw_overrides
                    .push(format!("approval_policy={}", toml_string(value)?));
                argv.extend([
                    "-c".to_string(),
                    format!("approval_policy={}", toml_string(value)?),
                ]);
            }
            if root.web_search {
                exec.config_overrides
                    .raw_overrides
                    .push("web_search=\"live\"".to_string());
                argv.extend(["-c".to_string(), "web_search=\"live\"".to_string()]);
            }
            for (enabled, flag) in [
                (self.json, "--json"),
                (self.ephemeral, "--ephemeral"),
                (self.skip_git_repo_check, "--skip-git-repo-check"),
            ] {
                if enabled {
                    argv.push(flag.to_string());
                }
            }
            for (path, flag) in [
                (&exec.output_schema, "--output-schema"),
                (&exec.last_message_file, "--output-last-message"),
            ] {
                if let Some(path) = path {
                    argv.extend([flag.to_string(), path.to_string_lossy().into_owned()]);
                }
            }
            TeamSession::Exec(exec)
        } else {
            if let Some(approval) = root.approval_policy {
                argv.extend([
                    "--ask-for-approval".to_string(),
                    approval_name(approval).to_string(),
                ]);
            }
            for (enabled, flag) in [
                (root.web_search, "--search"),
                (root.no_alt_screen, "--no-alt-screen"),
                (root.no_daemon, "--no-daemon"),
            ] {
                if enabled {
                    argv.push(flag.to_string());
                }
            }
            TeamSession::Interactive(root)
        };
        argv.extend(["--".to_string(), prompt.clone()]);
        let status = format!(
            "Team roles configured: architect={architect_model}/{}, engineer={}/{}, gatekeeper={}/{}, skeptic={}/{}",
            self.architect_effort,
            self.engineer_model,
            self.engineer_effort,
            self.gatekeeper_model,
            self.gatekeeper_effort,
            self.skeptic_model,
            self.skeptic_effort
        );
        Ok(TeamLaunch {
            session,
            dry_run: self.dry_run,
            plan: TeamPlan {
                argv,
                role_files,
                root_briefing: ROOT_BRIEFING,
                prompt,
                status,
            },
        })
    }
}

fn read_task(
    prompt_file: Option<&Path>,
    prompt: Option<String>,
    exec_mode: bool,
    input: &mut impl Read,
    stdin_is_terminal: bool,
) -> anyhow::Result<Option<String>> {
    let task =
        match prompt_file {
            Some(path) if path.as_os_str() != std::ffi::OsStr::new("-") => {
                let path = expand_home(path)?;
                Some(std::fs::read_to_string(&path).with_context(|| {
                    format!("could not read UTF-8 task file: {}", path.display())
                })?)
            }
            _ if prompt_file.is_some()
                || prompt.as_deref() == Some("-")
                || (exec_mode && prompt.is_none()) =>
            {
                if stdin_is_terminal {
                    anyhow::bail!(
                        "provide a task with --prompt, --prompt-file, or pipe a task into stdin"
                    );
                }
                let mut task = String::new();
                input
                    .read_to_string(&mut task)
                    .context("could not read a UTF-8 task from stdin")?;
                Some(task)
            }
            _ => prompt,
        };
    if task.as_ref().is_some_and(|task| task.trim().is_empty()) {
        anyhow::bail!("team task must not be blank");
    }
    if task.as_ref().is_some_and(|task| task.contains('\0')) {
        anyhow::bail!("team task must not contain NUL characters");
    }
    Ok(task)
}

fn validate_model(model: &str, name: &str) -> anyhow::Result<()> {
    if model.is_empty()
        || model.chars().count() > 256
        || model.starts_with('-')
        || model
            .chars()
            .any(|ch| ch.is_whitespace() || ch.is_control())
    {
        anyhow::bail!(
            "--{name}-model must be a model slug without whitespace or control characters (up to 256 characters)"
        );
    }
    Ok(())
}

fn expand_home(path: &Path) -> anyhow::Result<PathBuf> {
    if let Ok(suffix) = path.strip_prefix("~") {
        let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
            .context("could not resolve home directory")?;
        Ok(PathBuf::from(home).join(suffix))
    } else {
        Ok(path.to_owned())
    }
}

fn toml_string(value: &str) -> anyhow::Result<String> {
    // JSON escaping is valid in TOML basic strings, including control characters.
    Ok(serde_json::to_string(value)?)
}

fn role_text(
    name: &str,
    description: &str,
    instructions: &str,
    model: &str,
    effort: &str,
) -> anyhow::Result<String> {
    let mut text = String::new();
    for (key, value) in [
        ("name", name),
        ("description", description),
        ("developer_instructions", instructions),
        ("model", model),
        ("model_reasoning_effort", effort),
    ] {
        text.push_str(&format!("{key} = {}\n", toml_string(value)?));
    }
    Ok(text)
}

fn approval_name(approval: ApprovalModeCliArg) -> &'static str {
    match approval {
        ApprovalModeCliArg::OnRequest => "on-request",
        ApprovalModeCliArg::Never => "never",
    }
}

fn append_shared_argv(argv: &mut Vec<String>, interactive: &TuiCli) -> anyhow::Result<()> {
    let shared = &interactive.shared;
    for (flag, value) in [
        (
            "-C",
            shared
                .cwd
                .as_ref()
                .map(|path| path.to_string_lossy().into_owned()),
        ),
        ("-m", shared.model.clone()),
        ("--local-provider", shared.oss_provider.clone()),
        (
            "--profile",
            shared.config_profile_v2.as_ref().map(ToString::to_string),
        ),
    ] {
        if let Some(value) = value {
            argv.extend([flag.to_string(), value]);
        }
    }
    if let Some(sandbox) = shared.sandbox_mode {
        let value = match sandbox {
            codex_utils_cli::SandboxModeCliArg::ReadOnly => "read-only",
            codex_utils_cli::SandboxModeCliArg::WorkspaceWrite => "workspace-write",
            codex_utils_cli::SandboxModeCliArg::DangerFullAccess => "danger-full-access",
        };
        argv.extend(["--sandbox".to_string(), value.to_string()]);
    }
    for (enabled, flag) in [
        (shared.oss, "--oss"),
        (
            shared.dangerously_bypass_approvals_and_sandbox,
            "--dangerously-bypass-approvals-and-sandbox",
        ),
        (shared.bypass_hook_trust, "--dangerously-bypass-hook-trust"),
        (shared.worktree, "--worktree"),
        (interactive.strict_config, "--strict-config"),
    ] {
        if enabled {
            argv.push(flag.to_string());
        }
    }
    for (paths, flag) in [(&shared.images, "--image"), (&shared.add_dir, "--add-dir")] {
        for path in paths {
            argv.extend([flag.to_string(), path.to_string_lossy().into_owned()]);
        }
    }
    for value in &interactive.config_overrides.raw_overrides {
        argv.extend(["-c".to_string(), value.clone()]);
    }
    Ok(())
}

impl TeamPlan {
    pub(crate) fn print_preview(&self) -> anyhow::Result<()> {
        println!("{}", serde_json::to_string_pretty(self)?);
        Ok(())
    }

    pub(crate) fn print_status(&self) {
        eprintln!("{}", self.status);
    }

    pub(crate) fn publish(&self) -> anyhow::Result<()> {
        for (path, contents) in &self.role_files {
            let parent = path.parent().context("role file has no parent")?;
            let root = parent.parent().context("role directory has no parent")?;
            reject_symlinks(path, root)?;
            std::fs::create_dir_all(parent)?;
            reject_symlinks(path, root)?;
            let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
            temporary.write_all(contents.as_bytes())?;
            match temporary.persist_noclobber(path) {
                Ok(_) => {}
                Err(error) if error.error.kind() == std::io::ErrorKind::AlreadyExists => {
                    let metadata = std::fs::symlink_metadata(path)?;
                    if !metadata.is_file() || std::fs::read(path)? != contents.as_bytes() {
                        anyhow::bail!("existing role file differs: {}", path.display());
                    }
                }
                Err(error) => return Err(error.error.into()),
            }
        }
        Ok(())
    }
}

fn reject_symlinks(path: &Path, root: &Path) -> anyhow::Result<()> {
    for component in path.ancestors().take_while(|path| *path != root) {
        match std::fs::symlink_metadata(component) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                anyhow::bail!("state path contains a symlink: {}", component.display())
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error.into()),
        }
    }
    Ok(())
}

#[cfg(test)]
#[path = "team_cmd_tests.rs"]
mod tests;

// Keep the team workflow consistent with scripts/harness_team.py.
const ROOT_BRIEFING: &str = "You are the lead architect and final arbiter for this task. Use the engineer, gatekeeper, and skeptic roles for both implementation and informational tasks. For implementation, define a task contract before delegating: task ID, goal, owned paths, scope, architectural invariants, checks, acceptance criteria, and required evidence. Obtain a gatekeeper review of proposed tool parameters, paths, and relevant linters; delegate production code and shell work to the engineer; and obtain a skeptic review of the resulting diff and evidence before finalizing. Ask the skeptic to attack boundaries and races with actionable reproductions. Adjudicate disagreements, settle merge conflicts while preserving others' work, and repeat review until acceptance criteria hold. For informational questions, ask the engineer for a first draft, the gatekeeper for a relevant quick consistency check and review of any proposed tool parameters or paths, and the skeptic to challenge assumptions and boundary claims. Consolidate their findings into the final answer. Do not request shell tools for a conceptual question without a concrete need. The gatekeeper's advice never replaces native Codex enforcement and has no guaranteed latency. Report evidence and remaining limits. Use native spawn_agent agent_type values engineer, gatekeeper, and skeptic with fresh contexts; full-history forks may reject role selection on V1. Reuse role agents for follow-up work. This team workflow explicitly requests those delegations, subject to higher-priority session policy. When no task was supplied, acknowledge the configuration, wait for a task, and do not spawn agents.";
const ENGINEER_DESCRIPTION: &str =
    "Autonomous engineer for implementation and first drafts of informational answers.";
const ENGINEER_INSTRUCTIONS: &str = "Follow the architect's task contract. For implementation, own the assigned paths, write production code and meaningful tests, run appropriate checks, and report changed files, commands, results, and remaining risks. For informational questions, draft a clear answer with the assumptions and evidence needed for review; do not run shell tools for a conceptual question without a concrete need. Work inside the inherited sandbox and approval policy. Other agents may edit the same checkout; preserve their work and coordinate before touching paths outside your ownership.";
const GATEKEEPER_DESCRIPTION: &str =
    "Fast advisory gatekeeper for tool reviews and answer consistency checks.";
const GATEKEEPER_INSTRUCTIONS: &str = "Review the exact proposed tool arguments, target paths, and permissions when tools are proposed. Run relevant cheap linters for implementation tasks when permitted. For informational questions, check the draft for relevant internal consistency and whether a quick source check is needed; avoid needless shell commands. Return a concise allow, revise, or escalate recommendation with the reason. Escalate ambiguity or material risk to the architect. This advice does not replace or disable Codex sandbox, policy, approval, or automatic review enforcement. Favor speed but never claim a guaranteed response time.";
const SKEPTIC_DESCRIPTION: &str =
    "Adversarial reviewer for diffs, assumptions, and unsupported claims.";
const SKEPTIC_INSTRUCTIONS: &str = "Challenge the implementation or answer and its evidence. For code tasks, inspect the diff, boundary inputs, races, error paths, and security assumptions. For informational questions, challenge assumptions, ambiguous terms, edge cases, and overconfident claims. Give actionable findings or corrections, with file anchors and minimal reproductions when relevant. Distinguish verified failures from hypotheses. Do not edit the engineer's owned files unless the architect explicitly assigns a fix.";
