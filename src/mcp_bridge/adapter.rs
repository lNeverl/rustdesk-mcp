use crate::{client::QualityStatus, ui_session_interface::InvokeUiSession};
use base::message_proto::{
    terminal_response::Union, CursorData, CursorPosition, DisplayInfo, FileEntry, PeerInfo,
    SwitchDisplay, TerminalResponse, WindowsSession,
};
use hbb_common::rendezvous_proto::ConnType;
use serde_json::{json, Value};
use std::{
    io::Read,
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::SyncSender,
        Arc,
    },
};

#[derive(Clone, Default)]
pub(crate) struct BridgeUi {
    pub session_id: String,
    pub channel: &'static str,
    pub events: Option<SyncSender<Value>>,
    pub lost: Arc<AtomicBool>,
}

impl BridgeUi {
    fn emit(&self, kind: &str, data: Value) {
        if let Some(events) = &self.events {
            if events
                .try_send(json!({"jsonrpc":"2.0", "method":"event", "params":{
                    "session_id": self.session_id, "channel":self.channel, "type":kind, "data":data
                }}))
                .is_err()
            {
                self.lost.store(true, Ordering::SeqCst);
            }
        }
    }
}

impl InvokeUiSession for BridgeUi {
    fn set_peer_info(&self, pi: &PeerInfo) {
        self.emit(
            "authenticated",
            json!({"version":pi.version, "platform":pi.platform,
            "hostname":pi.hostname, "username":pi.username}),
        );
    }
    fn set_permission(&self, name: &str, value: bool) {
        self.emit("permission", json!({"name":name,"enabled":value}));
    }
    fn set_connection_type(&self, secured: bool, direct: bool, stream_type: &str) {
        self.emit(
            "transport",
            json!({"secured":secured,"direct":direct,"stream_type":stream_type}),
        );
    }
    fn msgbox(&self, kind: &str, title: &str, text: &str, _link: &str, _retry: bool) {
        self.emit(
            "challenge",
            json!({"kind":kind,"title":title,"message":text}),
        );
    }
    fn handle_terminal_response(&self, response: TerminalResponse) {
        match response.union {
            Some(Union::Opened(v)) => self.emit(
                "terminal.opened",
                json!({
                    "terminal_id":v.terminal_id,"success":v.success,"message":v.message,
                    "pid":v.pid,"replay":v.replay_terminal_output,
                    "persistent_sessions":v.persistent_sessions.len()
                }),
            ),
            Some(Union::Data(v)) => {
                let decoded = if v.compressed {
                    zstd::Decoder::new(v.data.as_ref()).and_then(|decoder| {
                        let mut bytes = Vec::new();
                        decoder.take(1024 * 1024 + 1).read_to_end(&mut bytes)?;
                        Ok(bytes)
                    })
                } else {
                    Ok(v.data.to_vec())
                };
                match decoded {
                    Ok(bytes) if bytes.len() <= 1024 * 1024 => self.emit(
                        "terminal.data",
                        json!({
                            "terminal_id":v.terminal_id,"data":crate::encode64(&bytes)
                        }),
                    ),
                    _ => self.lost.store(true, Ordering::SeqCst),
                }
            }
            Some(Union::Closed(v)) => self.emit(
                "terminal.closed",
                json!({
                    "terminal_id":v.terminal_id,"exit_code":v.exit_code
                }),
            ),
            Some(Union::Error(v)) => self.emit(
                "terminal.error",
                json!({
                    "terminal_id":v.terminal_id,"message":v.message
                }),
            ),
            _ => self.emit("unsupported_event", json!({})),
        }
    }

    // The terminal connection never requests desktop, clipboard, voice or file capabilities.
    fn set_cursor_data(&self, _: CursorData) {}
    fn set_cursor_id(&self, _: String) {}
    fn set_cursor_position(&self, _: CursorPosition) {}
    fn set_display(&self, _: i32, _: i32, _: i32, _: i32, _: bool, _: f64) {}
    fn switch_display(&self, _: &SwitchDisplay) {}
    fn set_displays(&self, _: &Vec<DisplayInfo>) {}
    fn set_platform_additions(&self, _: &str) {}
    fn on_connected(&self, _: ConnType) {}
    fn update_privacy_mode(&self) {}
    fn close_success(&self) {}
    fn update_quality_status(&self, _: QualityStatus) {}
    fn set_fingerprint(&self, _: String) {}
    fn job_error(&self, id: i32, error: String, file_num: i32) {
        self.emit(
            "files.error",
            json!({"job_id":id,"message":error,"file_num":file_num}),
        );
    }
    fn job_done(&self, id: i32, file_num: i32) {
        self.emit("files.done", json!({"job_id":id,"file_num":file_num}));
    }
    fn clear_all_jobs(&self) {}
    fn new_message(&self, _: String) {}
    fn update_transfer_list(&self) {}
    fn load_last_job(&self, _: i32, _: &str, _: bool) {}
    fn update_folder_files(
        &self,
        id: i32,
        entries: &Vec<FileEntry>,
        path: String,
        local: bool,
        _: bool,
    ) {
        let items: Vec<Value> = entries
            .iter()
            .take(4096)
            .map(|entry| {
                json!({
                    "name":entry.name,"type":entry.entry_type.value(),"size":entry.size,
                    "modified_time":entry.modified_time,"hidden":entry.is_hidden
                })
            })
            .collect();
        self.emit("files.directory", json!({"job_id":id,"path":path,"local":local,"entries":items,"truncated":entries.len()>4096}));
    }
    fn confirm_delete_files(&self, _: i32, _: i32, _: String) {}
    fn override_file_confirm(&self, id: i32, file_num: i32, _: String, upload: bool, _: bool) {
        self.emit(
            "files.overwrite_required",
            json!({"job_id":id,"file_num":file_num,"upload":upload}),
        );
    }
    fn update_block_input_state(&self, _: bool) {}
    fn job_progress(&self, id: i32, file_num: i32, speed: f64, finished_size: f64) {
        self.emit(
            "files.progress",
            json!({"job_id":id,"file_num":file_num,"bytes":finished_size,"bytes_per_second":speed}),
        );
    }
    fn adapt_size(&self) {}
    fn on_rgba(&self, _: usize, _: &mut scrap::ImageRgb) {}
    fn cancel_msgbox(&self, _: &str) {}
    fn switch_back(&self, _: &str) {}
    fn portable_service_running(&self, _: bool) {}
    fn on_voice_call_started(&self) {}
    fn on_voice_call_closed(&self, _: &str) {}
    fn on_voice_call_waiting(&self) {}
    fn on_voice_call_incoming(&self) {}
    fn get_rgba(&self, _: usize) -> *const u8 {
        std::ptr::null()
    }
    fn next_rgba(&self, _: usize) {}
    #[cfg(all(feature = "vram", feature = "flutter"))]
    fn on_texture(&self, _: usize, _: *mut std::ffi::c_void) {}
    fn set_multiple_windows_session(&self, _: Vec<WindowsSession>) {
        self.emit("session_selection_required", json!({}));
    }
    fn set_current_display(&self, _: i32) {}
    #[cfg(feature = "flutter")]
    fn is_multi_ui_session(&self) -> bool {
        false
    }
    fn update_record_status(&self, _: bool) {}
    fn printer_request(&self, _: i32, _: String) {}
    fn handle_screenshot_resp(&self, _: String, _: String) {}
}
