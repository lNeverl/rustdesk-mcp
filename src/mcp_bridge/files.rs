use super::{adapter::BridgeUi, fault, string};
use crate::{client::Data, ui_session_interface::Session};
use base::{
    fs::JobType,
    message_proto::{FileAction, FileRemoveDir, Message, ReadDir},
};
use hbb_common::{config::PeerConfig, rendezvous_proto::ConnType};
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::{Arc, RwLock},
};

pub(super) struct Source {
    pub login_id: String,
    pub peer: PeerConfig,
    pub force_relay: bool,
}

pub(super) struct Files {
    session: Session<BridgeUi>,
    authenticated: bool,
    secured: bool,
}

impl Drop for Files {
    fn drop(&mut self) {
        self.session.close();
    }
}

impl Files {
    pub fn start(source: &Source, password: &str, ui: &BridgeUi) -> Self {
        let session = Session {
            password: password.to_owned(),
            ui_handler: BridgeUi {
                channel: "files",
                ..ui.clone()
            },
            server_file_transfer_enabled: Arc::new(RwLock::new(true)),
            ..Default::default()
        };
        {
            let mut lc = session.lc.write().unwrap();
            lc.mcp_bridge = true;
            lc.save_config(source.peer.clone());
            lc.initialize(
                source.login_id.clone(),
                ConnType::FILE_TRANSFER,
                None,
                source.force_relay,
                None,
                None,
                None,
            );
            lc.remember = false;
        }
        session.reconnect(false);
        Self {
            session,
            authenticated: false,
            secured: false,
        }
    }

    pub fn event(&mut self, event: &Value) -> bool {
        let data = &event["params"]["data"];
        match event["params"]["type"].as_str().unwrap_or_default() {
            "authenticated" => self.authenticated = true,
            "transport" => {
                self.secured = data["secured"].as_bool() == Some(true);
                if !self.secured {
                    return false;
                }
            }
            "challenge" if !matches!(data["kind"].as_str(), Some("success" | "info")) => {
                return false
            }
            _ => {}
        }
        true
    }

    pub fn request(&self, method: &str, params: &Value) -> Result<Value, Value> {
        if method == "files.status" {
            return Ok(json!({"authenticated":self.authenticated,"secured":self.secured}));
        }
        if !self.authenticated || !self.secured {
            return Err(fault("not_authenticated"));
        }
        let id = params
            .get("job_id")
            .and_then(Value::as_i64)
            .filter(|id| (1..=i32::MAX as i64).contains(id))
            .ok_or_else(|| fault("invalid_params"))? as i32;
        let data = match method {
            "files.upload" | "files.download" => {
                let local = string(params, "local_path")?;
                let remote = string(params, "remote_path")?;
                if !Path::new(local).is_absolute() {
                    return Err(fault("invalid_path"));
                }
                if method == "files.upload" && !Path::new(local).is_file() {
                    return Err(fault("local_file_unavailable"));
                }
                Data::SendFiles((
                    id,
                    JobType::Generic,
                    if method == "files.upload" {
                        local
                    } else {
                        remote
                    }
                    .to_owned(),
                    if method == "files.upload" {
                        remote
                    } else {
                        local
                    }
                    .to_owned(),
                    0,
                    true,
                    method == "files.download",
                ))
            }
            "files.list" => {
                let mut action = FileAction::new();
                action.set_read_dir(ReadDir {
                    path: string(params, "remote_path")?.to_owned(),
                    include_hidden: true,
                    ..Default::default()
                });
                let mut message = Message::new();
                message.set_file_action(action);
                Data::Message(message)
            }
            "files.cancel" => Data::CancelJob(id),
            "files.mkdir" => Data::CreateDir((id, string(params, "remote_path")?.to_owned(), true)),
            "files.remove" => {
                let mut action = FileAction::new();
                if params["directory"] == true {
                    action.set_remove_dir(FileRemoveDir {
                        id,
                        path: string(params, "remote_path")?.to_owned(),
                        recursive: false,
                        ..Default::default()
                    });
                    let mut message = Message::new();
                    message.set_file_action(action);
                    Data::Message(message)
                } else {
                    Data::RemoveFile((id, string(params, "remote_path")?.to_owned(), 0, true))
                }
            }
            "files.rename" => Data::RenameFile((
                id,
                string(params, "remote_path")?.to_owned(),
                string(params, "new_name")?.to_owned(),
                true,
            )),
            // All transfers target a new owned staging file; an overwrite challenge is a failure.
            _ => return Err(fault("method_not_found")),
        };
        self.session
            .sender
            .read()
            .unwrap()
            .as_ref()
            .ok_or_else(|| fault("session_closed"))?
            .send(data)
            .map_err(|_| fault("session_closed"))?;
        Ok(json!({"submitted":true,"job_id":id}))
    }
}
