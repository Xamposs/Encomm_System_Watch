/// ENCOMM SYSTEM WATCH — Tauri desktop shell entry point.
///
/// Phase 2: the shell now manages the Python/FastAPI backend lifecycle:
///   * on startup it reuses an already-healthy backend, or launches the
///     project's own backend (backend\.venv) and waits for /api/health;
///   * on exit it stops ONLY the backend process it started.
///
/// The shell remains strictly read-only: it launches exactly one known
/// process (`backend\.venv\Scripts\python.exe -m uvicorn app.main:app
/// --host 127.0.0.1 --port 8765`) and exposes no commands, no shell and no
/// remote-control surface. Only a message dialog is used, to surface
/// startup errors the frontend could never know about.
mod backend_manager;

use std::sync::Mutex;
use tauri::RunEvent;
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};

/// Singleton slot for the BackendManager so the exit path (run loop /
/// after-run / at_exit) can stop an owned backend without an `App` handle.
static BACKEND: Mutex<Option<backend_manager::Manager>> = Mutex::new(None);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            // Runs after the window exists (the frontend already shows its
            // boot state) but before the event loop — this is where the
            // backend is adopted or started, and where startup failures
            // surface as a clear message before anything else happens.
            match backend_manager::start_backend() {
                Ok(manager) => {
                    let mut guard = BACKEND.lock().unwrap_or_else(|p| p.into_inner());
                    *guard = Some(manager);
                }
                Err(e) => {
                    let msg = backend_manager::startup_error_text(&e);
                    backend_manager::append_desktop_log(format!(
                        "startup: FAILED — {0}",
                        backend_manager::startup_error_summary(&e)
                    ));
                    app.dialog()
                        .message(msg)
                        .title("ENCOMM SYSTEM WATCH — startup error")
                        .kind(MessageDialogKind::Error)
                        .blocking_show();
                    std::process::exit(1);
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running ENCOMM SYSTEM WATCH");

    app.run(|_app, event| {
        // Normal exit (last window closed, exit requested).
        if let RunEvent::Exit = event {
            stop_backend();
        }
    });

    // Belt and braces: `run()` returning without an Exit event (rare on
    // Windows) still cleans up.
    stop_backend();
}

/// Stop the owned backend if this app started it (idempotent).
fn stop_backend() {
    let manager: Option<backend_manager::Manager> = {
        let mut guard = BACKEND.lock().unwrap_or_else(|p| p.into_inner());
        (*guard).take()
    };
    if let Some(mut m) = manager {
        m.stop();
    }
}