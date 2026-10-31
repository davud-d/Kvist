// Kvist's pixel-art desktop interface. The window is a page (../ui); this
// side owns what a page cannot: the core processes. Any number of agents run
// at once, each its own `--frontend stdio-multi` core in its own session.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod core;
mod ops;
mod window;

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::mpsc;
use std::time::Duration;

use serde_json::Value;
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

/// A core that has not said `hello` by then is not going to.
const HANDSHAKE_WAIT: Duration = Duration::from_secs(30);

#[derive(Default)]
struct Agents {
    cores: Mutex<HashMap<String, core::Core>>,
    /// Starting a core reads the workspace's active session, so two starts in
    /// one workspace must not overlap: each finishes its handshake first.
    opening: Mutex<()>,
}

fn locked<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    // A panic in another command leaves the data usable; the page keeps working.
    mutex.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn core_argv() -> Result<Vec<String>, String> {
    core::command(std::env::var("KVIST_CORE_CMD").ok().as_deref())
}

fn existing_dir(workspace: &str) -> Result<PathBuf, String> {
    let path = PathBuf::from(workspace);
    if path.is_dir() { Ok(path) } else { Err(format!("{workspace} is not a folder")) }
}

impl Agents {
    fn open(&self, app: AppHandle, id: String, workspace: &Path, session: Option<String>) -> Result<(), String> {
        let argv = core_argv()?;
        let _one_at_a_time = locked(&self.opening);
        if locked(&self.cores).contains_key(&id) {
            return Err(format!("agent {id} is already running"));
        }
        let pin = match &session {
            Some(name) => ops::Request { op: "switch", session_name: Some(name), history_limit: None, history_before: None, stdin: None },
            None => ops::Request { op: "launch", session_name: None, history_limit: None, history_before: None, stdin: None },
        };
        ops::run(&argv, workspace, &pin)?;
        let (ready, handshake) = mpsc::channel();
        let mut core = core::Core::spawn(app, &id, &argv, workspace, ready).map_err(|error| format!("could not start the core: {error}"))?;
        if handshake.recv_timeout(HANDSHAKE_WAIT).is_err() {
            core.kill();
            return Err("the core did not answer in time".to_string());
        }
        locked(&self.cores).insert(id, core);
        Ok(())
    }

    fn send(&self, id: &str, message: &Value) -> Result<(), String> {
        let mut cores = locked(&self.cores);
        let core = cores.get_mut(id).ok_or_else(|| format!("agent {id} is not running"))?;
        core.send(message).map_err(|error| error.to_string())
    }

    fn close(&self, id: &str) {
        let core = locked(&self.cores).remove(id);
        drop(core);
    }

    fn close_all(&self) {
        let cores: Vec<core::Core> = locked(&self.cores).drain().map(|(_, core)| core).collect();
        drop(cores);
    }
}

#[tauri::command]
async fn agent_open(app: AppHandle, id: String, workspace: String, session: Option<String>) -> Result<(), String> {
    let path = existing_dir(&workspace)?;
    tauri::async_runtime::spawn_blocking(move || {
        let agents = app.state::<Agents>();
        agents.open(app.clone(), id, &path, session)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
fn agent_send(agents: tauri::State<'_, Agents>, id: String, message: Value) -> Result<(), String> {
    agents.send(&id, &message)
}

#[tauri::command]
async fn agent_close(app: AppHandle, id: String) -> Result<(), String> {
    // Dropping a core waits for it to finish, which can take a moment.
    tauri::async_runtime::spawn_blocking(move || app.state::<Agents>().close(&id))
        .await
        .map_err(|error| error.to_string())
}

#[tauri::command]
async fn session_op(
    workspace: String,
    op: String,
    session_name: Option<String>,
    history_limit: Option<u32>,
    history_before: Option<u64>,
    stdin: Option<String>,
) -> Result<Value, String> {
    if !ops::page_may_run(&op) {
        return Err(format!("the page may not run the session operation {op}"));
    }
    let path = existing_dir(&workspace)?;
    let argv = core_argv()?;
    tauri::async_runtime::spawn_blocking(move || {
        let request = ops::Request {
            op: &op,
            session_name: session_name.as_deref(),
            history_limit,
            history_before,
            stdin: stdin.as_deref(),
        };
        ops::run(&argv, &path, &request)
    })
    .await
    .map_err(|error| error.to_string())?
}

/// The folder the first agent opens in: where the app was launched, unless that
/// is a home folder or a disk top, which are too wide to hand an agent.
#[tauri::command]
fn launch_dir() -> Result<String, String> {
    let here = std::env::current_dir().map_err(|error| error.to_string())?;
    let home = std::env::var_os("HOME").map(PathBuf::from);
    let too_wide = here.parent().is_none() || home.as_deref() == Some(here.as_path());
    if !too_wide {
        return Ok(here.display().to_string());
    }
    let fallback = home.ok_or("no home folder")?.join("Documents").join("Kvist Workspace");
    std::fs::create_dir_all(&fallback).map_err(|error| error.to_string())?;
    Ok(fallback.display().to_string())
}

/// How this window was made, so the page can drop its glass when there is none.
#[tauri::command]
fn window_mode(mode: tauri::State<'_, window::Mode>) -> Value {
    serde_json::json!({"transparent": mode.transparent, "safe": mode.safe})
}

/// Saved for the next start: transparency cannot change on a window that exists.
#[tauri::command]
fn set_transparent(value: bool) -> Result<(), String> {
    let path = window::settings_path(&|name| std::env::var(name).ok()).ok_or("no folder to save the setting in")?;
    window::save(&path, value).map_err(|error| error.to_string())
}

#[tauri::command]
fn restart_app(app: AppHandle) {
    app.restart();
}

fn create_window(app: &tauri::App, transparent: bool) -> tauri::Result<()> {
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("Kvist")
        .inner_size(1320.0, 840.0)
        .min_inner_size(880.0, 560.0)
        .decorations(false)
        .resizable(true)
        .transparent(transparent)
        .build()?;
    Ok(())
}

fn main() {
    let env = |name: &str| std::env::var(name).ok();
    let args: Vec<String> = std::env::args().skip(1).collect();
    let saved = window::settings_path(&env).and_then(|path| window::load_saved(&path));
    let mode = window::decide(&args, &env, saved, window::TRANSPARENT_BY_DEFAULT);
    window::prepare_renderer(mode, &env);
    run(mode);
}

fn run(mode: window::Mode) {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Agents::default())
        .manage(mode)
        .setup(move |app| Ok(create_window(app, mode.transparent)?))
        .invoke_handler(tauri::generate_handler![agent_open, agent_send, agent_close, session_op, launch_dir, window_mode, set_transparent, restart_app])
        .build(tauri::generate_context!())
        .expect("could not build the Kvist window");
    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            handle.state::<Agents>().close_all();
        }
    });
}
