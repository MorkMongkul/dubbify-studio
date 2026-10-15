use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// The first launch unpacks the bundled Python runtime, which can take a while.
const READY_TIMEOUT: Duration = Duration::from_secs(180);
/// Homebrew's folders: an app opened from Finder or the Dock gets
/// PATH=/usr/bin:/bin:/usr/sbin:/sbin, so a Homebrew ffmpeg would be invisible.
const HOMEBREW_PATHS: &str = "/opt/homebrew/bin:/usr/local/bin";
/// The sidecar's stdout/stderr log is rotated past this size.
const LOG_ROTATE_BYTES: u64 = 10_000_000;

#[derive(Default)]
struct Backend {
    child: Mutex<Option<CommandChild>>,
    /// Set while the app quits, so the backend's exit isn't reported as a crash.
    exiting: AtomicBool,
    /// Set once the backend process has ended, so start-up stops waiting for it.
    terminated: AtomicBool,
}

/// Payload of the `backend-failed` / `backend-stopped` events the window listens for.
#[derive(Clone, serde::Serialize)]
struct BackendProblem {
    message: String,
    log_dir: String,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        // Native Save-As dialog for video export (used from the webview)
        .plugin(tauri_plugin_dialog::init())
        .manage(Backend::default())
        .setup(|app| {
            app.handle().plugin(
                tauri_plugin_log::Builder::default()
                    .level(log::LevelFilter::Info)
                    .build(),
            )?;

            if cfg!(debug_assertions) {
                // Dev mode: the frontend is served by `npm run dev` (Vite) at
                // devUrl, proxying /api to a backend the developer runs
                // separately (`uvicorn app.main:app`). No sidecar to spawn here.
                return Ok(());
            }
            start_backend(app.handle())?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            // Cmd+Q arrives as Exit without ExitRequested on macOS — stop the
            // backend on either, or it would outlive the app.
            if matches!(event, tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit) {
                stop_backend(app);
            }
        });
}

/// Spawn the bundled FastAPI sidecar, then point the window at it once THIS
/// launch's backend answers. Everything the user creates lives in the
/// app-data folder; API keys come from the settings file there.
fn start_backend(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let data_dir = app.path().app_data_dir()?;
    let log_dir = app.path().app_log_dir()?;
    fs::create_dir_all(&data_dir)?;
    fs::create_dir_all(&log_dir)?;

    // A fresh free port and instance id per launch: a backend left over from
    // an earlier launch can neither block this one nor be mistaken for it.
    let port = free_port()?;
    let instance = instance_id();
    let version = app.package_info().version.to_string();
    let log_path = rotated_log(&log_dir);
    append_log(&log_path, &format!("\n──── launch {instance} · v{version} · port {port} ────\n"));

    let mut command = app
        .shell()
        .sidecar("dubify-backend")?
        .env("PORT", port.to_string())
        // Without this Python block-buffers stdout into the pipe and the log stays empty.
        .env("PYTHONUNBUFFERED", "1")
        .env("PATH", path_with_homebrew())
        .env("DUBIFY_DATA_DIR", &data_dir)
        .env("DUBIFY_LOG_DIR", &log_dir)
        .env("DUBIFY_INSTANCE_ID", &instance)
        .env("DUBIFY_APP_VERSION", &version)
        // The backend watches its stdin and exits when this app is gone.
        .env("DUBIFY_EXIT_WITH_PARENT", "1");
    for (tool, var) in [("ffmpeg", "FFMPEG_PATH"), ("ffprobe", "FFPROBE_PATH")] {
        if let Some(path) = bundled_tool(tool) {
            command = command.env(var, path);
        }
    }
    let (mut rx, child) = command.spawn()?;
    *app.state::<Backend>().child.lock().unwrap() = Some(child);
    log::info!("dubify-backend started on port {port}; logs in {}", log_dir.display());

    // Mirror the sidecar's output to a log file, and tell the window if it dies.
    let handle = app.clone();
    let log_dir_text = log_dir.display().to_string();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) | CommandEvent::Stderr(bytes) => {
                    append_log(&log_path, &String::from_utf8_lossy(&bytes));
                }
                CommandEvent::Error(err) => append_log(&log_path, &format!("[sidecar error] {err}\n")),
                CommandEvent::Terminated(payload) => {
                    append_log(&log_path, &format!("[sidecar terminated] {payload:?}\n"));
                    let backend = handle.state::<Backend>();
                    backend.terminated.store(true, Ordering::SeqCst);
                    if !backend.exiting.load(Ordering::SeqCst) {
                        let _ = handle.emit(
                            "backend-stopped",
                            BackendProblem {
                                message: format!(
                                    "The backend stopped unexpectedly (exit code {}).",
                                    payload.code.map_or("unknown".to_string(), |c| c.to_string())
                                ),
                                log_dir: log_dir_text.clone(),
                            },
                        );
                    }
                }
                _ => {}
            }
        }
    });

    let window = app.get_webview_window("main").ok_or("main window not found")?;
    let handle = app.clone();
    let log_dir_text = log_dir.display().to_string();
    std::thread::spawn(move || {
        let started = Instant::now();
        while started.elapsed() < READY_TIMEOUT {
            if handle.state::<Backend>().terminated.load(Ordering::SeqCst) {
                return; // already reported as backend-stopped
            }
            if backend_ready(port, &instance) {
                let _ = window.eval(&format!("window.location.replace('http://127.0.0.1:{port}/')"));
                return;
            }
            std::thread::sleep(Duration::from_millis(400));
        }
        log::error!("dubify-backend did not become ready within {}s", READY_TIMEOUT.as_secs());
        let _ = window.emit(
            "backend-failed",
            BackendProblem {
                message: format!("The backend didn't start within {} seconds.", READY_TIMEOUT.as_secs()),
                log_dir: log_dir_text,
            },
        );
    });
    Ok(())
}

fn stop_backend(app: &AppHandle) {
    let backend = app.state::<Backend>();
    backend.exiting.store(true, Ordering::SeqCst);
    // Killing the child also drops our end of its stdin; the Python process
    // (a child of the PyInstaller launcher) sees end-of-file and exits too.
    let child = backend.child.lock().unwrap().take();
    if let Some(child) = child {
        let _ = child.kill();
    }
}

fn free_port() -> std::io::Result<u16> {
    Ok(TcpListener::bind("127.0.0.1:0")?.local_addr()?.port())
}

fn instance_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or_default();
    format!("{nanos:x}-{:x}", std::process::id())
}

/// True once the backend on `port` answers /health/ready with this launch's
/// instance id — anything else listening there doesn't count.
fn backend_ready(port: u16, instance: &str) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let request = format!(
        "GET /health/ready HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    let _ = stream.read_to_string(&mut response);
    response.contains(" 200 ") && response.contains(instance)
}

fn path_with_homebrew() -> String {
    match std::env::var("PATH") {
        Ok(path) if !path.is_empty() => format!("{path}:{HOMEBREW_PATHS}"),
        _ => format!("/usr/bin:/bin:/usr/sbin:/sbin:{HOMEBREW_PATHS}"),
    }
}

/// ffmpeg/ffprobe bundled next to the app's executable (Contents/MacOS), if this build ships them.
fn bundled_tool(name: &str) -> Option<PathBuf> {
    let path = std::env::current_exe().ok()?.parent()?.join(name);
    path.is_file().then_some(path)
}

fn rotated_log(log_dir: &Path) -> PathBuf {
    let path = log_dir.join("sidecar.log");
    if fs::metadata(&path).map(|m| m.len() > LOG_ROTATE_BYTES).unwrap_or(false) {
        let _ = fs::rename(&path, log_dir.join("sidecar.log.1"));
    }
    path
}

fn append_log(path: &Path, text: &str) {
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = file.write_all(text.as_bytes());
    }
}
