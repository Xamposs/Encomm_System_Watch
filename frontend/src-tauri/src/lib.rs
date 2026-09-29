/// ENCOMM SYSTEM WATCH — Tauri desktop shell entry point (Phase 1).
///
/// The window loads the existing React/Vite frontend (see tauri.conf.json).
/// No Rust commands or plugins are exposed: the shell is a passive, strictly
/// read-only WebView2 window. The Python/FastAPI backend on 127.0.0.1:8765
/// remains the single source of truth for all observability data.
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running ENCOMM SYSTEM WATCH");
}