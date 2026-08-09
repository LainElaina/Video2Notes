//! Native Windows process boundary for the local Video2Notes desktop app.
//!
//! The Python service remains loopback-only.  This module creates an in-memory
//! session token, gives it to the child through an environment variable (never
//! through argv), and exposes it only to this app's webview via a Tauri command.

use std::{
    env,
    fs::{self, OpenOptions},
    net::{SocketAddr, TcpListener, TcpStream},
    path::{Component, Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    time::Duration,
};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use serde::Serialize;
use serde_json::Value;
use tauri::{AppHandle, Manager, RunEvent};

const DEFAULT_PORT: u16 = 43119;
const PORT_SEARCH_LIMIT: u16 = 32;
const TOKEN_ENVIRONMENT_VARIABLE: &str = "VIDEO2NOTES_TOKEN";
const DATA_ROOT_ENVIRONMENT_VARIABLE: &str = "VIDEO2NOTES_DATA_ROOT";
const BACKEND_EXECUTABLE_ENVIRONMENT_VARIABLE: &str = "VIDEO2NOTES_BACKEND_EXECUTABLE";

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct BackendConnection {
    base_url: String,
    token: String,
    backend_status: &'static str,
    data_root: String,
    backend_error: Option<String>,
    diagnostic_log: Option<String>,
}

#[derive(Debug)]
struct BackendState {
    token: String,
    port: u16,
    data_root: PathBuf,
    child: Option<Child>,
    status: BackendLifecycle,
    last_error: Option<String>,
    diagnostic_log: Option<PathBuf>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum BackendLifecycle {
    Starting,
    Ready,
    Offline,
}

impl BackendLifecycle {
    const fn as_str(self) -> &'static str {
        match self {
            Self::Starting => "starting",
            Self::Ready => "ready",
            Self::Offline => "offline",
        }
    }
}

/// Managed only by the native process.  It deliberately has no log output:
/// session tokens must never be persisted or printed by the desktop shell.
struct BackendManager(Mutex<BackendState>);

impl BackendManager {
    fn launch(app: &AppHandle) -> Self {
        let data_root = resolve_data_root(app);
        let port = find_loopback_port().unwrap_or(DEFAULT_PORT);
        let mut state = BackendState {
            token: String::new(),
            port,
            data_root,
            child: None,
            status: BackendLifecycle::Offline,
            last_error: None,
            diagnostic_log: None,
        };

        let Ok(token) = generate_session_token() else {
            state.last_error = Some("Could not create a secure local API session.".to_string());
            return Self(Mutex::new(state));
        };
        state.token = token;

        if let Some((executable, working_directory)) = find_backend_executable(app) {
            let mut command = Command::new(executable);
            command
                .arg("serve")
                .arg("--port")
                .arg(port.to_string())
                .arg("--data-root")
                .arg(&state.data_root)
                .env(TOKEN_ENVIRONMENT_VARIABLE, &state.token)
                .stdin(Stdio::null());
            if let Some(directory) = working_directory {
                command.current_dir(directory);
            }
            state.diagnostic_log = attach_backend_log(&mut command, &state.data_root);
            if state.diagnostic_log.is_none() {
                command.stdout(Stdio::null()).stderr(Stdio::null());
            }
            configure_hidden_windows_process(&mut command);
            match command.spawn() {
                Ok(child) => {
                    state.child = Some(child);
                    state.status = BackendLifecycle::Starting;
                }
                Err(error) => {
                    state.last_error = Some(format!("Could not start the local backend: {error}"));
                }
            }
        }

        Self(Mutex::new(state))
    }

    fn connection(&self) -> Result<BackendConnection, String> {
        let mut state = self
            .0
            .lock()
            .map_err(|_| "Video2Notes local backend state is unavailable.".to_string())?;
        refresh_backend_status(&mut state);
        if state.token.is_empty() {
            return Err("Video2Notes could not create a secure local API session.".to_string());
        }
        Ok(BackendConnection {
            base_url: format!("http://127.0.0.1:{}", state.port),
            token: state.token.clone(),
            backend_status: state.status.as_str(),
            data_root: state.data_root.to_string_lossy().into_owned(),
            backend_error: state.last_error.clone(),
            diagnostic_log: state
                .diagnostic_log
                .as_ref()
                .map(|path| path.to_string_lossy().into_owned()),
        })
    }

    fn data_root(&self) -> Result<PathBuf, String> {
        self.0
            .lock()
            .map(|state| state.data_root.clone())
            .map_err(|_| "Video2Notes local backend state is unavailable.".to_string())
    }

    fn shutdown(&self) {
        if let Ok(mut state) = self.0.lock() {
            if let Some(mut child) = state.child.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
            state.status = BackendLifecycle::Offline;
        }
    }
}

#[tauri::command]
fn runtime_label() -> &'static str {
    "Video2Notes desktop · Tauri 2"
}

/// Returns an in-memory, per-launch loopback API session for the current
/// desktop webview.  The UI should poll `/api/health` while `backendStatus` is
/// `starting`; an `offline` result is actionable without exposing internals.
#[tauri::command]
fn backend_connection(
    manager: tauri::State<'_, BackendManager>,
) -> Result<BackendConnection, String> {
    manager.connection()
}

/// Opens Windows' native file picker.  The selected path is absolute and is
/// only used as an input to the local pipeline; no file contents cross IPC.
#[tauri::command]
fn pick_video(locale: Option<String>) -> Option<String> {
    let is_english = locale.as_deref() == Some("en-US");
    rfd::FileDialog::new()
        .set_title(if is_english {
            "Select a local video"
        } else {
            "选择本地视频"
        })
        .add_filter(
            if is_english {
                "Video files"
            } else {
                "视频文件"
            },
            &["mp4", "mkv", "mov", "webm", "avi", "m4v", "flv", "wmv"],
        )
        .pick_file()
        .and_then(|path| path.canonicalize().ok())
        .map(|path| path.to_string_lossy().into_owned())
}

/// Opens the native directory picker for a self-describing runtime package.
/// The Python backend still validates `runtime-package.json`, hashes, platform
/// compatibility, and ownership before the directory can be registered.
#[tauri::command]
fn pick_runtime_directory(locale: Option<String>) -> Option<String> {
    rfd::FileDialog::new()
        .set_title(if locale.as_deref() == Some("en-US") {
            "Select a Video2Notes runtime package directory"
        } else {
            "选择 Video2Notes 运行时包目录"
        })
        .pick_folder()
        .and_then(|path| path.canonicalize().ok())
        .map(|path| path.to_string_lossy().into_owned())
}

/// Opens the native file picker for a user-owned executable or script. The
/// backend probes the selected file for the requested dependency before it is
/// persisted; selecting a file never grants Video2Notes ownership of it.
#[tauri::command]
fn pick_local_tool_file(locale: Option<String>) -> Option<String> {
    rfd::FileDialog::new()
        .set_title(if locale.as_deref() == Some("en-US") {
            "Select an installed program or script"
        } else {
            "选择已安装的程序或脚本"
        })
        .pick_file()
        .and_then(|path| path.canonicalize().ok())
        .map(|path| path.to_string_lossy().into_owned())
}

#[tauri::command]
fn pick_local_tool_directory(locale: Option<String>) -> Option<String> {
    rfd::FileDialog::new()
        .set_title(if locale.as_deref() == Some("en-US") {
            "Select a dependency or Python environment directory"
        } else {
            "选择依赖或 Python 环境目录"
        })
        .pick_folder()
        .and_then(|path| path.canonicalize().ok())
        .map(|path| path.to_string_lossy().into_owned())
}

/// Returns the self-authored, redistributable sample bundled with release
/// builds.  It exercises visual changes and OCR without any account or key.
#[tauri::command]
fn demo_video_path(app: AppHandle) -> Result<String, String> {
    let resources = app
        .path()
        .resource_dir()
        .map_err(|_| "The bundled demo directory is unavailable.".to_string())?;
    let sample = resources.join("demo").join("evidence-demo.mp4");
    sample
        .canonicalize()
        .map(|path| path.to_string_lossy().into_owned())
        .map_err(|_| "The bundled demo video is missing; rebuild the desktop package.".to_string())
}

/// Resolves an existing artifact to a local path only after checking that it
/// remains inside `DATA_ROOT/runs/<run_id>`.  This is intentionally narrower
/// than the Tauri asset protocol scope and rejects traversal and symlink hops.
#[tauri::command]
fn artifact_file_path(
    run_id: String,
    relative_path: String,
    manager: tauri::State<'_, BackendManager>,
) -> Result<String, String> {
    if !is_safe_run_id(&run_id) || !is_safe_relative_path(&relative_path) {
        return Err("The requested artifact path is not allowed.".to_string());
    }

    let data_root = manager.data_root()?;
    let runs_root = data_root.join("runs");
    let run_root = runs_root.join(&run_id);
    let candidate = run_root.join(&relative_path);
    if !candidate.is_file() {
        return Err("The requested artifact does not exist.".to_string());
    }

    let canonical_run_root = run_root
        .canonicalize()
        .map_err(|_| "The requested artifact does not exist.".to_string())?;
    let canonical_candidate = candidate
        .canonicalize()
        .map_err(|_| "The requested artifact does not exist.".to_string())?;
    if !canonical_candidate.starts_with(&canonical_run_root) {
        return Err("The requested artifact path is not allowed.".to_string());
    }
    Ok(canonical_candidate.to_string_lossy().into_owned())
}

fn resolve_data_root(app: &AppHandle) -> PathBuf {
    if let Some(value) = env::var_os(DATA_ROOT_ENVIRONMENT_VARIABLE) {
        if !value.is_empty() {
            let path = PathBuf::from(value);
            if fs::create_dir_all(&path).is_ok() {
                return path;
            }
        }
    }
    let data_root = app
        .path()
        .app_data_dir()
        .unwrap_or_else(|_| env::temp_dir().join("Video2Notes"));
    let _ = fs::create_dir_all(&data_root);
    data_root
}

fn attach_backend_log(command: &mut Command, data_root: &Path) -> Option<PathBuf> {
    let log_directory = data_root.join("logs");
    fs::create_dir_all(&log_directory).ok()?;
    let log_path = log_directory.join("backend-session.log");
    let output = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(&log_path)
        .ok()?;
    let error_output = output.try_clone().ok()?;
    command
        .stdout(Stdio::from(output))
        .stderr(Stdio::from(error_output));
    Some(log_path)
}

fn generate_session_token() -> Result<String, String> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|_| "Video2Notes could not create a secure local API session.".to_string())?;
    Ok(URL_SAFE_NO_PAD.encode(bytes))
}

fn find_loopback_port() -> Option<u16> {
    (DEFAULT_PORT..DEFAULT_PORT.saturating_add(PORT_SEARCH_LIMIT)).find(|port| {
        let address = SocketAddr::from(([127, 0, 0, 1], *port));
        TcpListener::bind(address).is_ok()
    })
}

fn find_backend_executable(app: &AppHandle) -> Option<(PathBuf, Option<PathBuf>)> {
    if let Some(value) = env::var_os(BACKEND_EXECUTABLE_ENVIRONMENT_VARIABLE) {
        let executable = PathBuf::from(value);
        return executable.is_file().then_some((executable, None));
    }

    let executable_name = if cfg!(windows) {
        "video2notes.exe"
    } else {
        "video2notes"
    };

    // Installed and portable release builds must exercise the frozen backend
    // shipped beside the desktop executable, even when a portable directory
    // happens to live inside a source checkout that also contains `.venv`.
    // Debug builds keep the opposite preference so `tauri dev` continues to
    // use the editable Python environment.
    let bundled_backend = find_bundled_backend_executable(app, executable_name);
    if !cfg!(debug_assertions) {
        if let Some(backend) = bundled_backend.clone() {
            return Some(backend);
        }
    }

    let mut roots = Vec::new();
    if let Ok(current) = env::current_dir() {
        roots.push(current);
    }
    if let Ok(current_exe) = env::current_exe() {
        if let Some(parent) = current_exe.parent() {
            roots.push(parent.to_path_buf());
        }
    }
    for root in roots {
        for ancestor in root.ancestors() {
            let candidate = ancestor.join(".venv").join("Scripts").join(executable_name);
            if candidate.is_file() {
                return Some((candidate, Some(ancestor.to_path_buf())));
            }
        }
    }

    if let Some(backend) = bundled_backend {
        return Some(backend);
    }

    // A normal installed `video2notes` command on PATH is the final fallback.
    Some((PathBuf::from(executable_name), None))
}

fn find_bundled_backend_executable(
    app: &AppHandle,
    executable_name: &str,
) -> Option<(PathBuf, Option<PathBuf>)> {
    let resources = app.path().resource_dir().ok()?;
    for candidate in [
        resources.join("backend").join(executable_name),
        resources.join(executable_name),
    ] {
        if candidate.is_file() {
            return Some((candidate, None));
        }
    }
    None
}

fn refresh_backend_status(state: &mut BackendState) {
    let Some(child) = state.child.as_mut() else {
        return;
    };
    match child.try_wait() {
        Ok(Some(exit_status)) => {
            state.last_error = state
                .diagnostic_log
                .as_deref()
                .and_then(latest_backend_error)
                .or_else(|| {
                    Some(match exit_status.code() {
                        Some(code) => format!("Local backend exited with code {code}."),
                        None => "Local backend exited before becoming ready.".to_string(),
                    })
                });
            state.child = None;
            state.status = BackendLifecycle::Offline;
        }
        Err(error) => {
            state.last_error = Some(format!("Could not inspect the local backend: {error}"));
            state.child = None;
            state.status = BackendLifecycle::Offline;
        }
        Ok(None) => {
            let address = SocketAddr::from(([127, 0, 0, 1], state.port));
            state.status =
                if TcpStream::connect_timeout(&address, Duration::from_millis(75)).is_ok() {
                    BackendLifecycle::Ready
                } else {
                    BackendLifecycle::Starting
                };
        }
    }
}

fn latest_backend_error(log_path: &Path) -> Option<String> {
    fs::read_to_string(log_path)
        .ok()
        .and_then(|content| parse_backend_error(&content))
}

fn parse_backend_error(content: &str) -> Option<String> {
    content.lines().rev().find_map(|line| {
        let payload = serde_json::from_str::<Value>(line).ok()?;
        if payload.get("event")?.as_str()? != "error" {
            return None;
        }
        let message = payload.get("message")?.as_str()?.trim();
        if message.is_empty() {
            return None;
        }
        let error_type = payload
            .get("error_type")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty());
        let detail = match error_type {
            Some(error_type) => format!("{error_type}: {message}"),
            None => message.to_string(),
        };
        Some(truncate_diagnostic(&detail, 800))
    })
}

fn truncate_diagnostic(value: &str, max_characters: usize) -> String {
    let mut characters = value.chars();
    let truncated: String = characters.by_ref().take(max_characters).collect();
    if characters.next().is_some() {
        format!("{truncated}...")
    } else {
        truncated
    }
}

fn is_safe_run_id(run_id: &str) -> bool {
    !run_id.is_empty()
        && run_id.len() <= 160
        && run_id.bytes().all(|character| {
            character.is_ascii_alphanumeric() || character == b'-' || character == b'_'
        })
}

fn is_safe_relative_path(value: &str) -> bool {
    let path = Path::new(value);
    !value.is_empty()
        && !path.is_absolute()
        && path
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
}

#[cfg(windows)]
fn configure_hidden_windows_process(command: &mut Command) {
    use std::os::windows::process::CommandExt;

    // CREATE_NO_WINDOW: backend stdout/stderr are already nulled; this also
    // prevents a transient console window while starting the Python launcher.
    command.creation_flags(0x0800_0000);
}

#[cfg(not(windows))]
fn configure_hidden_windows_process(_command: &mut Command) {}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .setup(|app| {
            let manager = BackendManager::launch(app.handle());
            // The static config covers the default app-data location.  Extend
            // it at runtime as well when VIDEO2NOTES_DATA_ROOT is explicitly
            // set, while keeping the scope restricted to completed run files.
            if let Ok(data_root) = manager.data_root() {
                app.asset_protocol_scope()
                    .allow_directory(data_root.join("runs"), true)?;
            }
            app.manage(manager);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            runtime_label,
            backend_connection,
            pick_video,
            pick_runtime_directory,
            pick_local_tool_file,
            pick_local_tool_directory,
            demo_video_path,
            artifact_file_path
        ])
        .build(tauri::generate_context!())
        .expect("failed to build Video2Notes desktop");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            app_handle.state::<BackendManager>().shutdown();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::parse_backend_error;

    #[test]
    fn extracts_latest_structured_backend_error_without_other_log_lines() {
        let content = concat!(
            "{\"event\":\"server_starting\",\"token_source\":\"environment\"}\n",
            "not json\n",
            "{\"event\":\"error\",\"error_type\":\"ValidationError\",",
            "\"message\":\"runtime catalog contains duplicate releases\"}\n",
        );

        assert_eq!(
            parse_backend_error(content).as_deref(),
            Some("ValidationError: runtime catalog contains duplicate releases")
        );
    }
}
