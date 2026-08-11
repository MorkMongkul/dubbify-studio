use std::fs::OpenOptions;
use std::io::Write;
use std::net::TcpStream;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::Manager;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// Where the sidecar's stdout/stderr get mirrored so it's inspectable even
/// when the app was launched by double-clicking (no attached terminal).
fn sidecar_log_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    let dir = PathBuf::from(home).join("Library/Logs/DubifyStudio");
    let _ = std::fs::create_dir_all(&dir);
    dir.join("sidecar.log")
}

// Fixed local port for the bundled FastAPI sidecar. Chosen to avoid common
// dev-server collisions (Vite's 5173/5174, the backend's own dev port 8000).
const SIDECAR_PORT: u16 = 8756;

struct SidecarState(Arc<Mutex<Option<CommandChild>>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let sidecar_state = SidecarState(Arc::new(Mutex::new(None)));

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        // Native Save-As dialog for video export (used from the webview)
        .plugin(tauri_plugin_dialog::init())
        .manage(sidecar_state)
        .setup(|app| {
            app.handle().plugin(
                tauri_plugin_log::Builder::default()
                    .level(log::LevelFilter::Info)
                    .build(),
            )?;

            if cfg!(debug_assertions) {
                // Dev mode: the frontend is served by `npm run dev` (Vite) at
                // devUrl, proxying /api to a backend the developer runs
                // separately (`fastapi dev` / `uvicorn`) — same workflow as
                // before Tauri. No sidecar to spawn here.
                return Ok(());
            }

            // Production: spawn the bundled Python/FastAPI sidecar, wait for
            // it to accept connections, then point the window at it so all
            // of the frontend's existing relative paths (/api/v1, /uploads,
            // video_url, output_url, ...) keep resolving against the same
            // origin exactly like they do today.
            let (mut rx, child) = app
                .shell()
                .sidecar("dubify-backend")
                .expect("failed to create sidecar command")
                .env("PORT", SIDECAR_PORT.to_string())
                // Without this Python block-buffers stdout when it's a pipe, so
                // the sidecar's startup log sat unflushed and sidecar.log read
                // as empty — making a slow/failed launch impossible to diagnose.
                .env("PYTHONUNBUFFERED", "1")
                .spawn()
                .expect("failed to spawn dubify-backend sidecar");

            let log_path = sidecar_log_path();
            log::info!("dubify-backend sidecar log: {}", log_path.display());
            tauri::async_runtime::spawn(async move {
                let mut file = OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&log_path)
                    .ok();
                while let Some(event) = rx.recv().await {
                    let line = match event {
                        CommandEvent::Stdout(bytes) => Some(String::from_utf8_lossy(&bytes).to_string()),
                        CommandEvent::Stderr(bytes) => Some(String::from_utf8_lossy(&bytes).to_string()),
                        CommandEvent::Error(err) => Some(format!("[sidecar error] {err}\n")),
                        CommandEvent::Terminated(payload) => {
                            Some(format!("[sidecar terminated] {:?}\n", payload))
                        }
                        _ => None,
                    };
                    if let (Some(line), Some(f)) = (line, file.as_mut()) {
                        let _ = f.write_all(line.as_bytes());
                        let _ = f.flush();
                    }
                }
            });

            let state = app.state::<SidecarState>();
            *state.0.lock().unwrap() = Some(child);

            let window = app
                .get_webview_window("main")
                .expect("main window not found");

            std::thread::spawn(move || {
                let addr = format!("127.0.0.1:{SIDECAR_PORT}");
                // A cold Neon database can take ~25s before the sidecar binds,
                // and a very cold one longer — 60s was tight enough to lose the
                // race and strand the webview on the asset protocol, where the
                // app renders but every /api call resolves to index.html.
                for _ in 0..360 {
                    if TcpStream::connect(&addr).is_ok() {
                        let url = format!("http://127.0.0.1:{SIDECAR_PORT}/");
                        let _ = window.eval(&format!("window.location.replace('{url}')"));
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(500));
                }
                log::error!("dubify-backend sidecar did not become ready within 180s");
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                let state = app_handle.state::<SidecarState>();
                let child_opt = state.0.lock().unwrap().take();
                if let Some(child) = child_opt {
                    let _ = child.kill();
                }
            }
        });
}
