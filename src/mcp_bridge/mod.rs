mod adapter;
mod files;
mod official;

use crate::{client::Data, ui_session_interface::Session};
use adapter::BridgeUi;
use hbb_common::{
    config::{Config2, PeerConfig},
    rendezvous_proto::ConnType,
};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    io::{self, BufRead, BufReader, Read, Write},
    net::{TcpListener, TcpStream},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver},
        Arc, RwLock,
    },
    thread,
    time::{Duration, Instant},
};

const MAX_FRAME: usize = 2 * 1024 * 1024;
const BRIDGE_VERSION: &str = "0.2.0";

pub(crate) fn isolate_config(config: &mut PeerConfig) {
    config.options.remove("terminal-service-id");
    config.options.remove("terminal-admin-service-id");
    config.terminal_persistent.v = false;
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Connect {
    peer_id: String,
    #[serde(default)]
    session_id: Option<String>,
    #[serde(default = "profile_source")]
    config_source: String,
    #[serde(default)]
    official_config_dir: Option<String>,
    #[serde(default)]
    server: String,
    #[serde(default)]
    server_key: String,
    #[serde(default)]
    password: String,
    #[serde(default)]
    os_username: String,
    #[serde(default)]
    os_password: String,
    #[serde(default)]
    elevate: bool,
    #[serde(default)]
    force_relay: bool,
}

fn profile_source() -> String {
    "profile".to_owned()
}

struct OwnedSession {
    id: String,
    session: Session<BridgeUi>,
    authenticated: bool,
    secured: bool,
    terminal_open: bool,
    os_username: String,
    os_password: String,
    login_sent: bool,
    file_source: files::Source,
    files: Option<files::Files>,
}

impl Drop for OwnedSession {
    fn drop(&mut self) {
        if self.terminal_open {
            self.session.close_terminal(1);
        }
        self.session.close();
    }
}

fn fault(code: &str) -> Value {
    json!({"code":-32000,"message":code,"data":{"code":code}})
}
fn string<'a>(params: &'a Value, key: &str) -> Result<&'a str, Value> {
    params
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| fault("invalid_params"))
}

fn dispatch(
    method: &str,
    params: Value,
    owned: &mut Option<OwnedSession>,
    ui: &BridgeUi,
) -> Result<Value, Value> {
    if method == "connect" {
        if owned.is_some() {
            return Err(fault("busy"));
        }
        if !hbb_common::config::Config::get_unlock_pin().is_empty() {
            return Err(fault("application_locked"));
        }
        let mut config: Connect =
            serde_json::from_value(params).map_err(|_| fault("invalid_params"))?;
        let peer_id: String = config
            .peer_id
            .chars()
            .filter(|c| !c.is_whitespace())
            .collect();
        if peer_id.is_empty() || !peer_id.chars().all(|c| c.is_ascii_digit()) {
            return Err(fault("invalid_peer_id"));
        }
        let (peer_config, network) = match config.config_source.as_str() {
            "profile" if !config.password.is_empty() => (PeerConfig::default(), Config2::default()),
            "profile" => return Err(fault("credential_unavailable")),
            "official" => {
                let snapshot = official::load(
                    &peer_id,
                    config.official_config_dir.as_deref(),
                    config.password.is_empty(),
                )?;
                config.server = snapshot.server;
                config.server_key = snapshot.key;
                (snapshot.peer, snapshot.network)
            }
            _ => return Err(fault("invalid_config_source")),
        };
        if config.elevate && (config.os_username.is_empty() || config.os_password.is_empty()) {
            return Err(fault("credential_unavailable"));
        }
        if config.server.contains(['@', '?', '&', '/', '\\'])
            || config.server_key.contains(['?', '&'])
        {
            return Err(fault("invalid_server"));
        }
        let login_id = if config.server.is_empty() {
            if config.config_source == "official" {
                format!("{peer_id}@public")
            } else {
                peer_id
            }
        } else {
            format!("{peer_id}@{}?key={}", config.server, config.server_key)
        };
        // Upstream network helpers use Config2; its file belongs to this bridge's namespace.
        Config2::set(network);
        let id = match config.session_id {
            Some(value) => uuid::Uuid::parse_str(&value)
                .map_err(|_| fault("invalid_params"))?
                .to_string(),
            None => uuid::Uuid::new_v4().to_string(),
        };
        let file_source = files::Source {
            login_id: login_id.clone(),
            peer: peer_config.clone(),
            force_relay: config.force_relay,
        };
        let session = Session {
            password: config.password,
            ui_handler: BridgeUi {
                session_id: id.clone(),
                ..ui.clone()
            },
            server_keyboard_enabled: Arc::new(RwLock::new(true)),
            ..Default::default()
        };
        {
            let mut lc = session.lc.write().unwrap();
            lc.mcp_bridge = true;
            lc.save_config(peer_config);
            lc.initialize(
                login_id,
                ConnType::TERMINAL,
                None,
                config.force_relay,
                None,
                None,
                None,
            );
            let config_snapshot = lc.load_config();
            lc.save_config(config_snapshot);
            lc.remember = false;
            lc.is_terminal_admin = config.elevate;
        }
        session.reconnect(false);
        *owned = Some(OwnedSession {
            id: id.clone(),
            session,
            authenticated: false,
            secured: false,
            terminal_open: false,
            os_username: config.os_username,
            os_password: config.os_password,
            login_sent: false,
            file_source,
            files: None,
        });
        return Ok(json!({"session_id":id,"state":"connecting","terminal_id":1}));
    }
    let session_id = string(&params, "session_id")?;
    if method == "disconnect" && owned.is_none() {
        return Ok(json!({"state":"disconnected","cleanup_confirmed":false}));
    }
    let current = owned.as_mut().ok_or_else(|| fault("session_closed"))?;
    if current.id != session_id {
        return Err(fault("session_closed"));
    }
    if method.starts_with("files.") {
        if !current.authenticated || !current.secured {
            return Err(fault("not_authenticated"));
        }
        if method == "files.connect" {
            if current.files.is_none() {
                current.files = Some(files::Files::start(
                    &current.file_source,
                    &current.session.password,
                    &current.session.ui_handler,
                ));
            }
            return Ok(json!({"submitted":true}));
        }
        if method == "files.disconnect" {
            current.files = None;
            return Ok(json!({"submitted":true}));
        }
        return current
            .files
            .as_ref()
            .ok_or_else(|| fault("files_session_closed"))?
            .request(method, &params);
    }
    match method {
        "disconnect" => {
            *owned = None;
            Ok(json!({"state":"disconnected","cleanup_confirmed":false}))
        }
        "status" => Ok(
            json!({"session_id":current.id,"authenticated":current.authenticated,
            "secured":current.secured,"terminal_open":current.terminal_open}),
        ),
        "terminal.open" => {
            if !current.authenticated || !current.secured {
                return Err(fault("not_authenticated"));
            }
            if current.terminal_open {
                return Err(fault("busy"));
            }
            current.session.open_terminal(1, 40, 160);
            Ok(json!({"submitted":true,"terminal_id":1}))
        }
        "terminal.write" => {
            if !current.terminal_open {
                return Err(fault("session_closed"));
            }
            let data = string(&params, "data")?;
            if data.len() > 256 * 1024 {
                return Err(fault("frame_too_large"));
            }
            let mut message = base::message_proto::Message::new();
            let mut action = base::message_proto::TerminalAction::new();
            action.set_data(base::message_proto::TerminalData {
                terminal_id: 1,
                data: data.as_bytes().to_vec().into(),
                ..Default::default()
            });
            message.set_terminal_action(action);
            let sender = current.session.sender.read().unwrap();
            sender
                .as_ref()
                .ok_or_else(|| fault("session_closed"))?
                .send(Data::Message(message))
                .map_err(|_| fault("session_closed"))?;
            Ok(json!({"submitted":true}))
        }
        "terminal.close" => {
            current.session.close_terminal(1);
            Ok(json!({"submitted":true,"termination_confirmed":false}))
        }
        "terminal.resize" => {
            let rows = params
                .get("rows")
                .and_then(Value::as_u64)
                .filter(|v| (1..=500).contains(v))
                .ok_or_else(|| fault("invalid_params"))?;
            let cols = params
                .get("cols")
                .and_then(Value::as_u64)
                .filter(|v| (1..=500).contains(v))
                .ok_or_else(|| fault("invalid_params"))?;
            current.session.resize_terminal(1, rows as u32, cols as u32);
            Ok(json!({"submitted":true}))
        }
        _ => Err(json!({"code":-32601,"message":"method_not_found"})),
    }
}

fn write_frame(stream: &mut TcpStream, value: &Value) -> io::Result<()> {
    serde_json::to_writer(&mut *stream, value)?;
    stream.write_all(b"\n")
}

fn read_requests(stream: TcpStream) -> Receiver<Option<Value>> {
    let (tx, rx) = mpsc::sync_channel(4);
    thread::spawn(move || {
        let mut reader = BufReader::new(stream);
        loop {
            let mut frame = Vec::new();
            let parsed = match reader
                .by_ref()
                .take((MAX_FRAME + 1) as u64)
                .read_until(b'\n', &mut frame)
            {
                Ok(n) if n > 0 && n <= MAX_FRAME && frame.last() == Some(&b'\n') => {
                    serde_json::from_slice(&frame).ok()
                }
                _ => None,
            };
            let done = parsed.is_none();
            if tx.send(parsed).is_err() || done {
                break;
            }
        }
    });
    rx
}

fn apply_event(event: &Value, current: &mut OwnedSession) -> bool {
    let params = &event["params"];
    if params["session_id"] != current.id {
        return true;
    }
    let data = &params["data"];
    if params["channel"] == "files" {
        if current
            .files
            .as_mut()
            .map(|files| !files.event(event))
            .unwrap_or(false)
        {
            current.files = None;
        }
        return true;
    }
    match params["type"].as_str().unwrap_or_default() {
        "transport" => {
            current.secured = data["secured"].as_bool() == Some(true);
            if !current.secured {
                return false;
            }
        }
        "authenticated" => current.authenticated = true,
        "terminal.opened" => {
            current.terminal_open = data["success"].as_bool() == Some(true);
            if !current.terminal_open
                || data["replay"] == true
                || data["persistent_sessions"].as_u64().unwrap_or(0) > 0
            {
                return false;
            }
        }
        "terminal.closed" => current.terminal_open = false,
        "terminal.error" | "session_selection_required" => return false,
        "challenge" => match data["kind"].as_str().unwrap_or_default() {
            "terminal-admin-login" | "terminal-admin-login-password" if !current.login_sent => {
                current.login_sent = true;
                current.session.login(
                    current.os_username.clone(),
                    current.os_password.clone(),
                    String::new(),
                    false,
                );
            }
            "success" | "info" => {}
            _ => return false,
        },
        _ => {}
    }
    true
}

pub fn run_cli() -> io::Result<()> {
    // Set the namespace before any upstream lazy configuration or logging is initialized.
    *hbb_common::config::APP_NAME.write().unwrap() = "RustDeskMCP".to_owned();
    if !crate::common::global_init() {
        return Err(io::Error::new(
            io::ErrorKind::Other,
            "initialization_failed",
        ));
    }
    hbb_common::init_log(false, "mcp-bridge");
    let result = run();
    crate::common::global_clean();
    result
}

fn run() -> io::Result<()> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    listener.set_nonblocking(true)?;
    let token = format!(
        "{}{}",
        uuid::Uuid::new_v4().simple(),
        uuid::Uuid::new_v4().simple()
    );
    let stopped = Arc::new(AtomicBool::new(false));
    let parent_eof = stopped.clone();
    thread::spawn(move || {
        let mut byte = [0u8; 1];
        loop {
            match io::stdin().read(&mut byte) {
                Ok(0) | Err(_) => break,
                _ => {}
            }
        }
        parent_eof.store(true, Ordering::SeqCst);
    });
    {
        let mut stdout = io::stdout().lock();
        serde_json::to_writer(
            &mut stdout,
            &json!({"type":"ready","protocol":{"major":1,"minor":1},
            "port":listener.local_addr()?.port(),"token":token,"instance_id":uuid::Uuid::new_v4().to_string(),
            "rustdesk_version":crate::VERSION,"bridge_version":BRIDGE_VERSION,
            "source_revision":option_env!("RUSTDESK_MCP_REVISION").unwrap_or("unknown"),
            "capabilities":["terminal","files","isolated_peer_config","official_config_readonly"]}),
        )?;
        stdout.write_all(b"\n")?;
        stdout.flush()?;
    }
    let started = Instant::now();
    let mut stream = loop {
        if stopped.load(Ordering::SeqCst) || started.elapsed() > Duration::from_secs(30) {
            return Ok(());
        }
        match listener.accept() {
            Ok((stream, _)) => break stream,
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(20))
            }
            Err(e) => return Err(e),
        }
    };
    stream.set_nonblocking(false)?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    let requests = read_requests(stream.try_clone()?);
    let (tx, events) = mpsc::sync_channel(256);
    let ui = BridgeUi {
        events: Some(tx),
        ..Default::default()
    };
    let mut owned: Option<OwnedSession> = None;
    let mut authenticated = false;
    while !stopped.load(Ordering::SeqCst) {
        if ui.lost.load(Ordering::SeqCst) {
            write_frame(
                &mut stream,
                &json!({"jsonrpc":"2.0","method":"event","params":{
                    "session_id":owned.as_ref().map(|s| &s.id),"type":"stream_lost","data":{}
                }}),
            )?;
            break;
        }
        while let Ok(event) = events.try_recv() {
            let keep = owned
                .as_mut()
                .map(|s| apply_event(&event, s))
                .unwrap_or(true);
            write_frame(&mut stream, &event)?;
            if !keep {
                let id = owned.as_ref().map(|s| s.id.clone());
                owned = None;
                write_frame(
                    &mut stream,
                    &json!({"jsonrpc":"2.0","method":"event","params":{
                        "session_id":id,"type":"session_closed","data":{"code":"remote_error"}
                    }}),
                )?;
            }
        }
        if !authenticated && started.elapsed() > Duration::from_secs(30) {
            break;
        }
        match requests.recv_timeout(Duration::from_millis(20)) {
            Ok(Some(request)) => {
                let id = request.get("id").cloned().unwrap_or(Value::Null);
                if request["jsonrpc"] != "2.0" || id.is_null() {
                    break;
                }
                let method = request["method"].as_str().unwrap_or_default();
                let params = request.get("params").cloned().unwrap_or_else(|| json!({}));
                let result = if !authenticated {
                    if method == "hello"
                        && params["token"].as_str() == Some(token.as_str())
                        && params["major"] == 1
                    {
                        authenticated = true;
                        Ok(json!({"protocol":{"major":1,"minor":1}}))
                    } else {
                        Err(fault("authentication_failed"))
                    }
                } else {
                    dispatch(method, params, &mut owned, &ui)
                };
                let response = match result {
                    Ok(result) => json!({"jsonrpc":"2.0","id":id,"result":result}),
                    Err(error) => json!({"jsonrpc":"2.0","id":id,"error":error}),
                };
                write_frame(&mut stream, &response)?;
                if !authenticated {
                    break;
                }
            }
            Ok(None) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
            Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
    }
    drop(owned);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bridge_config_never_restores_user_terminals() {
        let mut config = PeerConfig::default();
        config
            .options
            .insert("terminal-service-id".into(), "user-service".into());
        config
            .options
            .insert("terminal-admin-service-id".into(), "admin-service".into());
        config
            .options
            .insert("other-server-key".into(), "key".into());
        config.terminal_persistent.v = true;
        isolate_config(&mut config);
        assert!(!config.options.contains_key("terminal-service-id"));
        assert!(!config.options.contains_key("terminal-admin-service-id"));
        assert!(!config.terminal_persistent.v);
        assert_eq!(config.options["other-server-key"], "key");
    }
}
