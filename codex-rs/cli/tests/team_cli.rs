//! Process smoke coverage for the native team command, without model requests.

use anyhow::Result;
use pretty_assertions::assert_eq;
use serde_json::Value;
use std::path::PathBuf;
use std::process::Output;
use tempfile::TempDir;
use tokio::io::AsyncWriteExt;

struct TeamFixture {
    _root: TempDir,
    project: PathBuf,
    home: PathBuf,
    state: PathBuf,
}

impl TeamFixture {
    fn new() -> Result<Self> {
        let root = tempfile::tempdir()?;
        let project = root.path().join("project with spaces");
        let home = root.path().join("home");
        let state = project.join("team state");
        std::fs::create_dir(&project)?;
        std::fs::create_dir(&home)?;
        Ok(Self {
            _root: root,
            project,
            home,
            state,
        })
    }

    async fn run(&self, args: &[&str]) -> Result<Output> {
        self.run_with_input(args, &[]).await
    }

    async fn run_with_input(&self, args: &[&str], input: &[u8]) -> Result<Output> {
        let mut command = tokio::process::Command::new(codex_utils_cargo_bin::cargo_bin("codex")?);
        command
            .current_dir(&self.project)
            .env("CODEX_HOME", &self.home)
            .env_remove("CODEX_API_KEY")
            .env_remove("OPENAI_API_KEY")
            .env_remove("CODEX_ACCESS_TOKEN")
            .args(args)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);
        Ok(
            tokio::time::timeout(std::time::Duration::from_secs(20), async {
                let mut child = command.spawn()?;
                child
                    .stdin
                    .take()
                    .expect("piped stdin")
                    .write_all(input)
                    .await?;
                child.wait_with_output().await
            })
            .await??,
        )
    }
}

#[tokio::test]
async fn requested_team_command_produces_a_json_preview_without_role_files() -> Result<()> {
    let fixture = TeamFixture::new()?;
    let output = fixture
        .run(&[
            "team",
            "--architect-model",
            "gpt-6.1-sol",
            "--architect-effort",
            "ultra",
            "--prompt",
            "your task",
            "--state-dir",
            fixture.state.to_str().expect("UTF-8 fixture path"),
            "--dry-run",
        ])
        .await?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let preview: Value = serde_json::from_slice(&output.stdout)?;
    assert!(
        preview["prompt"]
            .as_str()
            .expect("prompt string")
            .ends_with("your task")
    );
    assert_eq!(
        preview["role_files"]
            .as_object()
            .expect("role mapping")
            .len(),
        3
    );
    assert!(!fixture.state.exists());
    assert!(
        !String::from_utf8_lossy(&output.stderr).contains("Team roles configured:"),
        "dry-run must not print the launch banner",
    );
    Ok(())
}

#[tokio::test]
async fn exec_preview_preserves_codex_flags_and_literal_task_text() -> Result<()> {
    let fixture = TeamFixture::new()?;
    let task = "Inspect $(touch never-created) and `printf surprise`\n--config=payload";
    let output = fixture
        .run(&[
            "--profile",
            "work",
            "team",
            "--exec",
            "--dry-run",
            "--sandbox=read-only",
            "--json",
            "--skip-git-repo-check",
            "--prompt",
            task,
        ])
        .await?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let preview: Value = serde_json::from_slice(&output.stdout)?;
    let argv = preview["argv"].as_array().expect("argument array");
    for expected in ["exec", "--json", "--skip-git-repo-check"] {
        assert!(
            argv.iter().any(|arg| arg.as_str() == Some(expected)),
            "missing {expected}"
        );
    }
    assert!(
        preview["prompt"]
            .as_str()
            .expect("prompt string")
            .ends_with(task)
    );
    assert!(!fixture.project.join("never-created").exists());
    assert!(!fixture.project.join(".codex-harness").exists());
    Ok(())
}

#[tokio::test]
async fn invalid_and_remote_team_commands_fail_before_publication() -> Result<()> {
    let fixture = TeamFixture::new()?;
    for args in [
        vec!["team", "--architect-effort", "unsupported"],
        vec!["team", "--architect-model", "invalid model"],
        vec![
            "--remote",
            "ws://127.0.0.1:1",
            "team",
            "--prompt",
            "Inspect",
        ],
    ] {
        let output = fixture.run(&args).await?;
        assert!(!output.status.success(), "unexpected success for {args:?}");
        assert!(output.stdout.is_empty(), "failure emitted a launch preview");
        assert!(!fixture.project.join(".codex-harness").exists());
    }
    Ok(())
}

#[tokio::test]
async fn task_files_and_stdin_are_tasks_in_interactive_and_exec_previews() -> Result<()> {
    let fixture = TeamFixture::new()?;
    let task = "Inspect $(touch never-created) and `printf surprise`\n--config=payload\n";
    std::fs::write(fixture.project.join("task with spaces.txt"), task)?;
    for args in [
        vec!["team", "--dry-run", "--prompt-file", "task with spaces.txt"],
        vec![
            "team",
            "--dry-run",
            "--exec",
            "--prompt-file",
            "task with spaces.txt",
        ],
        vec!["team", "--dry-run", "--prompt-file", "-"],
        vec!["team", "--dry-run", "--exec", "-"],
        vec!["team", "--dry-run", "--exec"],
    ] {
        let output = fixture.run_with_input(&args, task.as_bytes()).await?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let preview: Value = serde_json::from_slice(&output.stdout)?;
        let prompt = preview["prompt"].as_str().expect("task prompt");
        assert!(prompt.ends_with(task), "task missing for {args:?}");
        assert!(!prompt.contains("No task supplied yet"));
        assert!(!fixture.project.join(".codex-harness").exists());
        assert!(!fixture.project.join("never-created").exists());
    }
    Ok(())
}

#[tokio::test]
async fn unreadable_and_empty_tasks_fail_before_role_publication() -> Result<()> {
    let fixture = TeamFixture::new()?;
    for (args, input) in [
        (vec!["team", "--exec"], &b" \n\t"[..]),
        (vec!["team", "--exec", "-"], &b"\xff"[..]),
        (vec!["team", "--prompt-file", "missing.txt"], &b""[..]),
        (vec!["team", "--prompt", " "], &b""[..]),
    ] {
        let output = fixture.run_with_input(&args, input).await?;
        assert!(!output.status.success(), "unexpected success for {args:?}");
        assert!(output.stdout.is_empty());
        assert!(!output.stderr.is_empty());
        assert!(!fixture.project.join(".codex-harness").exists());
    }
    Ok(())
}

#[tokio::test]
async fn task_file_paths_use_launch_directory_when_session_cwd_differs() -> Result<()> {
    let fixture = TeamFixture::new()?;
    let task = "Inspect the project in the selected session directory";
    std::fs::write(fixture.project.join("task.txt"), task)?;
    let output = fixture
        .run(&[
            "team",
            "--dry-run",
            "--cwd",
            fixture.home.to_str().expect("UTF-8 directory"),
            "--prompt-file",
            "task.txt",
        ])
        .await?;
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let preview: Value = serde_json::from_slice(&output.stdout)?;
    assert!(
        preview["prompt"]
            .as_str()
            .expect("task prompt")
            .ends_with(task)
    );
    let cwd = fixture.home.canonicalize()?.to_string_lossy().into_owned();
    assert!(
        preview["argv"]
            .as_array()
            .expect("argument array")
            .windows(2)
            .any(|pair| pair[0] == "-C" && pair[1] == cwd)
    );
    assert!(!fixture.project.join(".codex-harness").exists());
    assert!(!fixture.home.join(".codex-harness").exists());
    Ok(())
}

#[tokio::test]
async fn root_model_and_explicit_architect_model_follow_subcommand_precedence() -> Result<()> {
    let fixture = TeamFixture::new()?;
    for (tail, expected) in [
        (vec![], "root-model"),
        (vec!["--architect-model", "child-model"], "child-model"),
        (vec!["-m", "child-alias"], "child-alias"),
    ] {
        let mut args = vec!["-m", "root-model", "team", "--dry-run"];
        args.extend(tail);
        let output = fixture.run(&args).await?;
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let preview: Value = serde_json::from_slice(&output.stdout)?;
        let argv = preview["argv"].as_array().expect("argument array");
        assert!(
            argv.windows(2)
                .any(|pair| pair[0] == "-m" && pair[1] == expected)
        );
    }
    Ok(())
}
