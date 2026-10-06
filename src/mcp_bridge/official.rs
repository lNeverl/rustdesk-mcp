use base::config::keys;
use hbb_common::{
    config::{Config2, PeerConfig, Socks5Server},
    directories_next::ProjectDirs,
    password_security::{decrypt_str_or_original, decrypt_vec_or_original},
    sodiumoxide::base64,
    toml,
};
use serde::{de::DeserializeOwned, Deserialize};
use serde_json::{json, Value};
use std::{collections::HashMap, fs, path::Path};

const PASSWORD_STORAGE_VERSION: &str = "00";

#[derive(Default, Deserialize)]
struct Settings {
    #[serde(default)]
    options: HashMap<String, String>,
    #[serde(default)]
    socks: Option<Socks5Server>,
    #[serde(default)]
    unlock_pin: String,
}

pub(super) struct Snapshot {
    pub server: String,
    pub key: String,
    pub peer: PeerConfig,
    pub network: Config2,
}

fn read<T: DeserializeOwned>(path: &Path) -> Result<T, Value> {
    let content =
        fs::read_to_string(path).map_err(|_| super::fault("official_config_unavailable"))?;
    toml::from_str(&content).map_err(|_| super::fault("official_config_invalid"))
}

pub(super) fn load(
    peer_id: &str,
    directory: Option<&str>,
    needs_password: bool,
) -> Result<Snapshot, Value> {
    let root = match directory {
        Some(path) => Path::new(path).to_path_buf(),
        None => ProjectDirs::from("", "", "RustDesk")
            .ok_or_else(|| super::fault("official_config_unavailable"))?
            .config_dir()
            .to_path_buf(),
    };
    let settings: Settings = read(&root.join("RustDesk2.toml"))?;
    if !settings.unlock_pin.is_empty() {
        return Err(super::fault("official_application_locked"));
    }
    let server = settings
        .options
        .get(keys::OPTION_CUSTOM_RENDEZVOUS_SERVER)
        .filter(|value| !value.is_empty())
        .cloned()
        .unwrap_or_default();
    let key = settings
        .options
        .get(keys::OPTION_KEY)
        .cloned()
        .unwrap_or_default();
    let path = root.join("peers").join(format!("{peer_id}.toml"));
    let mut peer: PeerConfig = if path.is_file() {
        read(&path)?
    } else if !server.is_empty() {
        let qualified = format!("{peer_id}@{server}");
        let filename = if qualified.contains(['<', '>', ':', '/', '\\', '|', '?', '*']) {
            format!(
                "base64_{}",
                base64::encode(qualified, base64::Variant::Original)
            )
        } else {
            qualified
        };
        let path = root.join("peers").join(format!("{filename}.toml"));
        if path.is_file() {
            read(&path)?
        } else {
            PeerConfig::default()
        }
    } else {
        PeerConfig::default()
    };
    if needs_password {
        peer.password = decrypt_vec_or_original(&peer.password, PASSWORD_STORAGE_VERSION).0;
        // Upstream remembers the salted SHA-256 hash, not a plaintext password.
        if peer.password.len() != 32 {
            return Err(super::fault("official_credential_unavailable"));
        }
    } else {
        peer.password.clear();
    }
    let mut options = HashMap::new();
    for name in [
        keys::OPTION_RELAY_SERVER,
        keys::OPTION_ALLOW_WEBSOCKET,
        keys::OPTION_ENABLE_WEBRTC,
        keys::OPTION_ICE_SERVERS,
        keys::OPTION_ENABLE_IPV6_PUNCH,
        keys::OPTION_RELAY_FALLBACK_DELAY,
    ] {
        if let Some(value) = settings.options.get(name) {
            options.insert(name, value);
        }
    }
    let mut socks = settings.socks;
    if let Some(proxy) = socks.as_mut() {
        let (password, decrypted, migrate) =
            decrypt_str_or_original(&proxy.password, PASSWORD_STORAGE_VERSION);
        if !password.is_empty() && !decrypted && !migrate {
            return Err(super::fault("official_credential_unavailable"));
        }
        proxy.password = password;
    }
    let network = serde_json::from_value(json!({"options":options,"socks":socks}))
        .map_err(|_| super::fault("official_config_invalid"))?;
    super::isolate_config(&mut peer);
    Ok(Snapshot {
        server,
        key,
        peer,
        network,
    })
}
