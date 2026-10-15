//! In-app updates. Once per launch (after the backend is up) and from
//! "Check for Updates…" in the app menu, the app reads latest.json from the
//! newest published GitHub release. When that version is newer it asks,
//! downloads the update (progress shows in the window title), checks its
//! signature against the public key in tauri.conf.json, installs it over this
//! copy and offers to restart.
//!
//! The app downloads the update itself, so macOS doesn't quarantine it and
//! people aren't asked to "Open Anyway" again.

use std::sync::atomic::{AtomicBool, Ordering};

use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

pub const MENU_ID: &str = "check-for-updates";
const WEBSITE: &str = "https://morkmongkul.github.io/dubbify-studio/";

/// One check at a time: the launch check and the menu item can overlap.
static BUSY: AtomicBool = AtomicBool::new(false);

#[derive(Clone, Copy, PartialEq)]
pub enum Trigger {
    /// Speaks up only when there is an update; being offline stays quiet.
    Launch,
    /// The person asked, so every outcome gets an answer.
    Menu,
}

enum Failure {
    /// Couldn't find out whether there is an update (offline, GitHub down…).
    Check(String),
    /// The person chose to install and it didn't work.
    Install(String),
}

pub fn check(app: &AppHandle, trigger: Trigger) {
    if BUSY.swap(true, Ordering::SeqCst) {
        return;
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let result = run(&app, trigger).await;
        BUSY.store(false, Ordering::SeqCst);
        let message = match result {
            Ok(()) => return,
            Err(Failure::Check(message)) if trigger == Trigger::Launch => {
                log::warn!("update check failed: {message}");
                return;
            }
            Err(Failure::Check(message) | Failure::Install(message)) => message,
        };
        log::error!("update failed: {message}");
        tell(
            &app,
            "Couldn't update Dubify Studio",
            format!(
                "{message}\n\nIf Dubify Studio isn't in your Applications folder, move it there and try again. \
                 You can also download the latest version from {WEBSITE}"
            ),
            MessageDialogKind::Error,
        )
        .await;
    });
}

async fn run(app: &AppHandle, trigger: Trigger) -> Result<(), Failure> {
    let current = app.package_info().version.to_string();
    let update = app
        .updater()
        .map_err(|e| Failure::Check(e.to_string()))?
        .check()
        .await
        .map_err(|e| Failure::Check(e.to_string()))?;

    let Some(update) = update else {
        log::info!("update check: {current} is the latest version");
        if trigger == Trigger::Menu {
            tell(
                app,
                "You're up to date",
                format!("Dubify Studio {current} is the latest version."),
                MessageDialogKind::Info,
            )
            .await;
        }
        return Ok(());
    };
    log::info!("update check: {} is available (running {current})", update.version);

    let install = ask(
        app,
        "Update available",
        format!(
            "Dubify Studio {} is available. You have {current}.\n\n\
             Download and install it now? Your projects aren't affected.",
            update.version
        ),
        "Install Update",
        "Not Now",
    )
    .await;
    if !install {
        return Ok(());
    }

    let window = app.get_webview_window("main");
    let title = window.as_ref().and_then(|w| w.title().ok()).unwrap_or_else(|| "Dubify Studio".into());
    let mut received: u64 = 0;
    let mut shown_percent = None;
    let installed = update
        .download_and_install(
            |chunk, total| {
                received += chunk as u64;
                let (Some(window), Some(total)) = (&window, total.filter(|t| *t > 0)) else {
                    return;
                };
                let percent = (received * 100 / total).min(100);
                if shown_percent != Some(percent) {
                    shown_percent = Some(percent);
                    let _ = window.set_title(&format!("{title} — downloading update {percent}%"));
                }
            },
            || {
                if let Some(window) = &window {
                    let _ = window.set_title(&format!("{title} — installing update…"));
                }
            },
        )
        .await;
    if let Some(window) = &window {
        let _ = window.set_title(&title);
    }
    installed.map_err(|e| Failure::Install(e.to_string()))?;
    log::info!("update: installed {}", update.version);

    let restart = ask(
        app,
        "Update installed",
        format!(
            "Dubify Studio {} is installed. Restart now to start using it.\n\n\
             If an export or voice generation is running, choose Later and restart when it's finished.",
            update.version
        ),
        "Restart Now",
        "Later",
    )
    .await;
    if restart {
        // Quitting stops the backend (see the RunEvent handler in lib.rs);
        // the new copy starts its own.
        app.restart();
    }
    Ok(())
}

/// A two-button question; true for `yes`.
async fn ask(app: &AppHandle, title: &str, message: String, yes: &str, no: &str) -> bool {
    if cfg!(feature = "updater-e2e") {
        log::info!("update: answered \"{yes}\" to \"{title}\" (updater-e2e test build)");
        return true;
    }
    let (app, title, yes, no) = (app.clone(), title.to_string(), yes.to_string(), no.to_string());
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .message(message)
            .title(title)
            .buttons(MessageDialogButtons::OkCancelCustom(yes, no))
            .blocking_show()
    })
    .await
    .unwrap_or(false)
}

async fn tell(app: &AppHandle, title: &str, message: String, kind: MessageDialogKind) {
    if cfg!(feature = "updater-e2e") {
        log::info!("update: would show \"{title}\": {message}");
        return;
    }
    let (app, title) = (app.clone(), title.to_string());
    let _ = tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .message(message)
            .title(title)
            .kind(kind)
            .buttons(MessageDialogButtons::Ok)
            .blocking_show()
    })
    .await;
}
