/// ENCOMM SYSTEM WATCH — backend lifecycle manager (Phase 2).
///
/// The desktop shell owns exactly ONE process: the project's own
/// Python/FastAPI backend (`backend\.venv\Scripts\python.exe -m uvicorn
/// app.main:app --host 127.0.0.1 --port 8765`).
///
/// Guarantees:
///   * never starts a second backend while a healthy one is already running;
///   * never kills a process it did not start (no PID sweeping);
///   * if the port is held by a foreign application it reports a clear error
///     and does nothing else;
///   * launches no other processes and exposes no shell/command surface;
///   * the child is tracked by its own `Child` handle (not by PID), so
///     shutdown can never hit a recycled PID of an unrelated process.
///
/// TLS is not needed on loopback: the health probe is a plain HTTP/1.1 GET
/// over `std::net::TcpStream` (no extra dependencies, trivially auditable).
use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddr, SocketAddrV4, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::Duration;

/// Backend contract — mirrored from Start-SystemWatch.ps1.
const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 8765;
const HEALTH_PATH: &str = "/api/health";
/// Total health-gate budget (matches the PowerShell launcher's ~45 s window).
const HEALTH_BUDGET: u32 = 90; // 90 x 500 ms
const HEALTH_POLL: Duration = Duration::from_millis(500);
/// Per-HTTP-attempt timeout (loopback; generous for cold collector init).
const HTTP_TIMEOUT: Duration = Duration::from_secs(2);
/// Grace window for a previous instance that is still booting: 20 x 500 ms.
const GRACE_POLLS: u32 = 20;

// ---------------------------------------------------------------------------
// Startup outcome / errors
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub enum StartupError {
    /// No project root found (no `backend\` marker anywhere above the exe).
    ProjectRootMissing,
    /// `backend\.venv` does not exist — setup is required.
    VenvMissing { python: String },
    /// Port 8765 is bound by something that never answers as SYSTEM WATCH.
    PortOccupiedForeign { pid: String },
    /// The known backend executable failed to spawn.
    SpawnFailed { detail: String },
    /// The spawned backend never became healthy (or exited during startup).
    HealthTimeout {
        exited: bool,
        stdout_tail: String,
        stderr_tail: String,
    },
}

/// Human-readable dialog text for the startup error.
pub fn startup_error_text(e: &StartupError) -> String {
    match e {
        StartupError::ProjectRootMissing => format!(
            "ENCOMM SYSTEM WATCH could not locate the project.\n\
             \n\
             Expected the desktop app to find backend\\.venv\\Scripts\\python.exe beside its own\n\
             binary (dev layout), or a backend\\ folder beside the app.\n\
             \n\
             Run Setup-SystemWatch.ps1 once in the project folder, or start the app with the\n\
             ESW_PROJECT_ROOT environment variable pointing at the project folder."
        ),
        StartupError::VenvMissing { python } => format!(
            "Backend environment not found.\n\
             \n\
             Expected: {0}\n\
             \n\
             Run Setup-SystemWatch.ps1 first (creates backend\\.venv and installs\n\
             dependencies). No packages are installed automatically at launch.",
            python
        ),
        StartupError::PortOccupiedForeign { pid } => format!(
            "Port 8765 is already in use by another application{0}.\n\
             \n\
             SYSTEM WATCH never kills foreign processes. Close the application that holds\n\
             127.0.0.1:8765 (or its other SYSTEM WATCH instance) and start again.",
            if pid.is_empty() {
                String::new()
            } else {
                format!(" (PID {0})", pid)
            }
        ),
        StartupError::SpawnFailed { detail } => format!(
            "Failed to start the backend:\n\n{0}\n\nSee backend\\esw-backend.err.log for details.",
            detail
        ),
        StartupError::HealthTimeout { exited, stdout_tail, stderr_tail } => format!(
            "The backend did not become healthy within 45 s.\n\n{0}\n\nDiagnostics:\n\
             --- backend\\esw-backend.log (tail) ---\n{1}\n\
             --- backend\\esw-backend.err.log (tail) ---\n{2}",
            if *exited {
                "The backend process exited during startup."
            } else {
                "The backend process is running but not responding to /api/health."
            },
            stdout_tail,
            stderr_tail
        ),
    }
}

/// Short one-line summary (used for backend\esw-desktop.log records).
pub fn startup_error_summary(e: &StartupError) -> String {
    match e {
        StartupError::ProjectRootMissing => "PROJECT_ROOT_MISSING".to_string(),
        StartupError::VenvMissing { .. } => "VENV_MISSING".to_string(),
        StartupError::PortOccupiedForeign { pid } => {
            format!("PORT_OCCUPIED_FOREIGN (pid={0})", pid)
        }
        StartupError::SpawnFailed { .. } => "SPAWN_FAILED".to_string(),
        StartupError::HealthTimeout { exited, .. } => {
            if *exited { "HEALTH_TIMEOUT_EXITED".to_string() } else { "HEALTH_TIMEOUT".to_string() }
        }
    }
}

// ---------------------------------------------------------------------------
// Manager
// ---------------------------------------------------------------------------

/// Lifecycle handle: whether we own the backend and — if we own it — the
/// `Child` handle created when it was spawned. `backend_owned == false`
/// means an existing healthy backend was reused and MUST be left untouched.
pub struct Manager {
    backend_owned: bool,
    backend_pid: u32,
    child: Option<Child>,
}

impl Manager {
    fn adopted(pid: u32) -> Manager {
        Manager { backend_owned: false, backend_pid: pid, child: None }
    }

    fn owned(pid: u32, child: Child) -> Manager {
        Manager { backend_owned: true, backend_pid: pid, child: Some(child) }
    }

    /// Stop the backend, but ONLY if this app started it. Idempotent — safe
    /// to call from the run loop and after run() returns.
    pub fn stop(&mut self) {
        if !self.backend_owned {
            append_desktop_log("exit: backend not owned by this app; leaving it untouched");
            return;
        }
        let mut child = self.child.take();
        if child.is_none() {
            return; // already stopped
        }
        let pid = self.backend_pid;
        append_desktop_log(format!("exit: stopping owned backend (pid={0})", pid));

        // 1) hard-stop the exact process we spawned (handle-based, immune to
        //    PID reuse). Same semantics as the launcher's taskkill /F.
        if let Some(c) = child.as_mut() {
            let _ = c.kill();
        }
        // 2) bounded reap — give the OS a moment to release the process.
        let mut reaped = false;
        if let Some(c) = child.as_mut() {
            for _ in 0..10 {
                std::thread::sleep(Duration::from_millis(100));
                if c.try_wait().map(|s| s.is_some()).unwrap_or(false) {
                    reaped = true;
                    break;
                }
            }
        }
        // 3) tree fallback for any grandchildren (uvicorn runs single-worker,
        //    but a late ETW child would otherwise linger) — targets the exact
        //    PID we own, never a sweep.
        if !reaped {
            let _ = Command::new("taskkill".to_string())
                .arg("/PID".to_string())
                .arg(pid.to_string())
                .arg("/T".to_string())
                .arg("/F".to_string())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .map(|mut c| {
                    let _ = c.wait();
                });
        }
        append_desktop_log(format!("exit: owned backend stopped (pid={0})", pid));
    }
}

impl Drop for Manager {
    fn drop(&mut self) {
        self.stop();
    }
}

// ---------------------------------------------------------------------------
// Public entry points
// ---------------------------------------------------------------------------

/// Start (or adopt) the backend and wait for health. Returns the Manager that
/// the caller (lib.rs) stores in its own exit-path slot.
pub fn start_backend() -> Result<Manager, StartupError> {
    let root = locate_project_root();
    if root.is_none() {
        return Err(StartupError::ProjectRootMissing);
    }
    let root = root.unwrap();

    let python = root.join("backend").join(".venv").join("Scripts").join("python.exe");
    if !python.is_file() {
        return Err(StartupError::VenvMissing { python: python.display().to_string() });
    }

    // --- reuse-or-conflict check (never kills anything) ---------------------
    match probe_with_grace() {
        Probe::Ok => {
            let pid = listener_pid(BACKEND_PORT).unwrap_or(0);
            append_desktop_log(format!("startup: adopted existing healthy backend (pid={0})", pid));
            Ok(Manager::adopted(pid))
        }
        Probe::Free => {
            append_desktop_log("startup: port free; starting owned backend");
            spawn_and_gate(&root, &python)
        }
        Probe::Bound => {
            let pid = listener_pid(BACKEND_PORT)
                .map(|p| format!("{0}", p))
                .unwrap_or_else(|| String::new());
            append_desktop_log(format!(
                "startup: failed — port occupied by foreign process (pid={0})",
                pid
            ));
            Err(StartupError::PortOccupiedForeign { pid })
        }
    }
}

// ---------------------------------------------------------------------------
// Health probe
// ---------------------------------------------------------------------------

enum Probe {
    /// A healthy SYSTEM WATCH backend is answering /api/health.
    Ok,
    /// Nothing is listening on 127.0.0.1:8765.
    Free,
    /// Something answers the port but not as a healthy SYSTEM WATCH.
    Bound,
}

/// One-shot probe: TCP connect then HTTP GET /api/health.
fn probe_once() -> Probe {
    let Ok(stream) = TcpStream::connect(SocketAddr::V4(SocketAddrV4::new(
        Ipv4Addr::new(127, 0, 0, 1),
        BACKEND_PORT,
    ))) else {
        // Nothing is listening on loopback: the port is free.
        return Probe::Free;
    };
    match http_get(&stream, HEALTH_PATH) {
        Ok(body) => {
            if health_body_ok(&body) {
                Probe::Ok
            } else {
                Probe::Bound
            }
        }
        Err(_) => Probe::Bound, // connected but never answered as SYSTEM WATCH
    }
}

/// Probe, allowing a short grace window for a previous instance that is still
/// booting (listening but not yet answering /api/health).
fn probe_with_grace() -> Probe {
    match probe_once() {
        Probe::Ok => Probe::Ok,
        Probe::Free => Probe::Free,
        Probe::Bound => {
            for _ in 0..GRACE_POLLS {
                std::thread::sleep(HEALTH_POLL);
                match probe_once() {
                    Probe::Bound => { /* keep waiting */ }
                    other => return other,
                }
            }
            Probe::Bound
        }
    }
}

/// True when the body JSON contains `"status":"ok"` (tolerant of whitespace).
fn health_body_ok(body: &str) -> bool {
    let b = body.as_bytes();
    let mut i = 0usize;
    while i + 8 <= b.len() {
        if b[i..(i + 8)] == *b"\"status\"" {
            let mut j = i + 8;
            while j < b.len() && b[j] == b' ' {
                j += 1;
            }
            if j + 1 < b.len() && b[j] == b':' {
                let mut k = j + 1;
                while k < b.len() && b[k] == b' ' {
                    k += 1;
                }
                if k + 4 <= b.len()
                    && b[k] == b'"'
                    && b[k + 1] == b'o'
                    && b[k + 2] == b'k'
                    && b[k + 3] == b'"'
                {
                    return true;
                }
            }
        }
        i += 1;
    }
    false
}

/// Minimal HTTP/1.1 GET over the caller-owned loopback stream. `Connection:
/// close` makes the server close the socket after the response, so EOF
/// delimits the body — no content-length/chunked parsing needed.
fn http_get(mut stream: &TcpStream, path: &str) -> Result<String, std::io::Error> {
    let req = format!(
        "GET {0} HTTP/1.1\r\nHost: {1}:{2}\r\nUser-Agent: encomm-system-watch-desktop/1.2\r\n\
         Connection: close\r\n\r\n",
        path, BACKEND_HOST, BACKEND_PORT,
    );
    stream.set_read_timeout(Some(HTTP_TIMEOUT))?;
    stream.set_write_timeout(Some(HTTP_TIMEOUT))?;

    let bytes = req.as_bytes();
    let mut off = 0usize;
    while off < bytes.len() {
        let end = (off + 4096).min(bytes.len());
        let n = stream.write(&bytes[off..end])?;
        if n == 0 {
            break;
        }
        off += n;
    }

    let mut resp = Vec::new();
    let mut chunk = [0u8; 4096];
    loop {
        let n = stream.read(&mut chunk)?;
        if n == 0 {
            break;
        }
        for b in chunk[..n].iter() {
            resp.push(*b);
        }
    }
    Ok(String::from_utf8_lossy(&resp).into_owned())
}

// ---------------------------------------------------------------------------
// Spawn + health gate
// ---------------------------------------------------------------------------

/// Spawn the known backend and gate on /api/health. On ANY failure the child
/// is stopped again so nothing is left behind.
fn spawn_and_gate(root: &PathBuf, python: &PathBuf) -> Result<Manager, StartupError> {
    let backend_dir = root.join("backend");
    let out_log = backend_dir.join("esw-backend.log");
    let err_log = backend_dir.join("esw-backend.err.log");

    // Truncate + redirect the log files (same behavior as the PowerShell
    // launcher). File -> Stdio::file is provided by `impl From<File> for Stdio`.
    let out_file = match File::create(out_log.clone()) {
        Ok(f) => f,
        Err(e) => {
            return Err(StartupError::SpawnFailed {
                detail: format!("cannot create esw-backend.log: {0}", e),
            });
        }
    };
    let err_file = match File::create(err_log.clone()) {
        Ok(f) => f,
        Err(e) => {
            return Err(StartupError::SpawnFailed {
                detail: format!("cannot create esw-backend.err.log: {0}", e),
            });
        }
    };

    // The venv must not inherit a leaking PYTHONPATH, and the launcher pins
    // the bind contract (127.0.0.1:8765) exactly like Start-SystemWatch.ps1.
    let spawned = Command::new(python.display().to_string())
        .current_dir(&backend_dir)
        .arg("-m".to_string())
        .arg("uvicorn".to_string())
        .arg("app.main:app".to_string())
        .arg("--host".to_string())
        .arg("127.0.0.1".to_string())
        .arg("--port".to_string())
        .arg("8765".to_string())
        .env("PYTHONPATH", "")
        .env("ESW_HOST", "127.0.0.1")
        .env("ESW_PORT", "8765")
        .env("PYTHONUNBUFFERED", "1")
        .stdout(out_file)
        .stderr(err_file)
        .spawn();

    let mut child = match spawned {
        Ok(c) => c,
        Err(err) => {
            let detail = err.to_string();
            append_desktop_log(format!("startup: spawn failed — {0}", detail));
            return Err(StartupError::SpawnFailed { detail });
        }
    };
    let pid = child.id();
    append_desktop_log(format!("startup: spawned owned backend (pid={0})", pid));

    // --- health gate (bounded, mirroring the ~45 s launcher window) --------
    for _ in 0..HEALTH_BUDGET {
        std::thread::sleep(HEALTH_POLL);
        if child.try_wait().map(|s| s.is_some()).unwrap_or(false) {
            // Backend process died before becoming healthy.
            let so = tail_file(&out_log, 25);
            let se = tail_file(&err_log, 25);
            append_desktop_log("startup: backend exited before health");
            let _ = child.kill(); // already dead; best-effort reap
            return Err(StartupError::HealthTimeout {
                exited: true,
                stdout_tail: so,
                stderr_tail: se,
            });
        }
        match probe_once() {
            Probe::Ok => {
                append_desktop_log(format!("startup: backend healthy (pid={0})", pid));
                return Ok(Manager::owned(pid, child));
            }
            // still booting — uvicorn binds after lifespan startup
            Probe::Free | Probe::Bound => {}
        }
    }

    // Timed out. Kill the owned child (never leave an orphan behind) and
    // surface the diagnostics.
    let so = tail_file(&out_log, 25);
    let se = tail_file(&err_log, 25);
    append_desktop_log("startup: health gate timed out; stopping owned backend");
    let _ = child.kill();
    for _ in 0..10 {
        std::thread::sleep(Duration::from_millis(100));
        if child.try_wait().map(|s| s.is_some()).unwrap_or(false) {
            break;
        }
    }
    Err(StartupError::HealthTimeout { exited: false, stdout_tail: so, stderr_tail: se })
}

// ---------------------------------------------------------------------------
// Path resolution (space-safe; PathBuf end to end)
// ---------------------------------------------------------------------------

fn locate_project_root() -> Option<PathBuf> {
    // Explicit override is authoritative (also used by packaged setups).
    if let Ok(s) = std::env::var("ESW_PROJECT_ROOT") {
        if !s.is_empty() {
            return Some(PathBuf::from(s));
        }
    }
    // Dev layout: walk up from the exe looking for project markers.
    let Ok(exe) = std::env::current_exe() else {
        return None;
    };
    let mut dir = exe.parent();
    let mut hops = 0usize;
    while hops < 9 {
        match dir {
            Some(d) => {
                if looks_like_project_root(&d) {
                    return Some(d.to_path_buf());
                }
                dir = d.parent();
            }
            None => break,
        }
        hops += 1;
    }
    None
}

/// A candidate root has a `backend\` folder and either the venv python or the
/// project's own launcher script (also marks a space-free dev copy).
fn looks_like_project_root(d: &Path) -> bool {
    if !d.join("backend").is_dir() {
        return false;
    }
    d.join("backend").join(".venv").join("Scripts").join("python.exe").is_file()
        || d.join("Start-SystemWatch.ps1").is_file()
}

// ---------------------------------------------------------------------------
// Diagnostics helpers (all read-only)
// ---------------------------------------------------------------------------

/// PID currently LISTENING on the given port (read-only netstat parse), or
/// None when nothing is listening / parsing fails. Used for error messages
/// and adoption records — never for killing.
fn listener_pid(port: u16) -> Option<u32> {
    let out = run_capture("netstat", &["-ano"]);
    if out.is_err() {
        return None;
    }
    let text = out.unwrap();
    let needle = format!(":{0}", port);
    let mut best: Option<u32> = None;
    let mut exact: Option<u32> = None;
    for line in text.split("\n") {
        if !line.contains("LISTENING") || !line.contains(&needle) {
            continue;
        }
        let toks: Vec<&str> = line.split_whitespace().collect();
        if toks.is_empty() {
            continue;
        }
        let last = match toks.last() {
            Some(t) => *t,
            None => "",
        };
        if let Some(pid) = digits_to_u32(last) {
            if line.starts_with("TCP")
                && line.contains(format!("{0}:{1}", BACKEND_HOST, port).as_str())
            {
                exact = Some(pid);
            } else if best.is_none() {
                best = Some(pid);
            }
        }
    }
    if exact.is_some() { exact } else { best }
}

/// ASCII-digit parse (no allocation, no radix-API assumptions).
fn digits_to_u32(tok: &str) -> Option<u32> {
    if tok.is_empty() {
        return None;
    }
    let mut acc: u32 = 0;
    for b in tok.as_bytes() {
        if !b.is_ascii_digit() {
            return None;
        }
        acc = acc * 10 + (b - b'0') as u32;
    }
    Some(acc)
}

/// Run a read-only diagnostic command and capture stdout (bounded pipes).
fn run_capture(prog: &str, args: &[&str]) -> Result<String, std::io::Error> {
    let child = Command::new(prog.to_string())
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    let mut buf = Vec::new();
    let mut chunk = [0u8; 4096];
    let reader = child.stdout;
    if let Some(mut r) = reader {
        loop {
            let n = r.read(&mut chunk)?;
            if n == 0 {
                break;
            }
            for b in chunk[..n].iter() {
                buf.push(*b);
            }
        }
    }
    // Windows reaps automatically; dropping the Child closes our handle.
    Ok(String::from_utf8_lossy(&buf).into_owned())
}

/// Last `max_lines` lines of a file (best effort; used for diagnostics).
fn tail_file(path: &PathBuf, max_lines: usize) -> String {
    let data = match std::fs::read(path) {
        Ok(d) => d,
        Err(_) => return format!("(log file not readable: {0})", path.display()),
    };
    let text = String::from_utf8_lossy(&data).into_owned();
    let lines: Vec<&str> = text.split("\n").collect();
    let start = if lines.len() >= max_lines { lines.len() - max_lines } else { 0 };
    let mut out = String::new();
    for i in start..lines.len() {
        out.push_str(lines[i]);
        out.push('\n');
    }
    out
}

/// Append one diagnostic line to backend\esw-desktop.log (git-ignored).
pub fn append_desktop_log<S: AsRef<str>>(line: S) {
    let line = line.as_ref();
    let root = locate_project_root();
    if root.is_none() {
        return;
    }
    let path = root.unwrap().join("backend").join("esw-desktop.log");
    let f = OpenOptions::new().append(true).create(true).open(&path);
    if let Ok(mut file) = f {
        let msg = format!("{0}\r\n", line);
        let _ = file.write_all(msg.as_bytes());
    }
}