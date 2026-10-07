import csv
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ToolError
from .windows import unprotect


def private_directory(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                                  check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        sid = next(csv.reader(identity.stdout.strip().splitlines()))[-1]
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                       capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        path.chmod(0o700)


@dataclass
class Config:
    bridge_exe: Path
    data_dir: Path
    allow_writes: bool = False
    profiles: dict = field(default_factory=dict, repr=False)
    admin_profiles: dict = field(default_factory=dict, repr=False)
    config_source: str = "profile"
    official_config_dir: Path | None = None
    remote_roots: list[str] = field(default_factory=list)
    max_sessions: int = 4
    max_transfer_bytes: int = 512 * 1024 * 1024
    storage_budget_bytes: int = 512 * 1024 * 1024
    downloads: dict = field(default_factory=dict, repr=False)
    intel: dict = field(default_factory=dict, repr=False)
    auto_approve: bool = False

    def __post_init__(self):
        if self.auto_approve:
            self.allow_writes = True

    @classmethod
    def load(cls, path: Path | None, bridge_exe: Path | None):
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "RustDeskMCP"
        if path is None:
            path = root / "config.json"
        raw = json.loads(path.read_text("utf-8-sig")) if path.exists() else {}
        executable = bridge_exe or Path(raw.get("bridge_exe", "rustdesk-mcp-bridge.exe"))
        source = raw.get("config_source", "profile")
        if source not in {"profile", "official"}:
            raise ToolError("invalid_config_source")
        official_dir = Path(raw["official_config_dir"]).resolve() if raw.get("official_config_dir") else None
        auto = (bool(raw.get("auto_approve", False)) or
                os.environ.get("RUSTDESK_MCP_AUTO_APPROVE", "").strip().lower() in {"1", "true", "yes", "on"})
        result = cls(executable.resolve(), root, bool(raw.get("allow_writes", False)) or auto,
                     raw.get("profiles", {}), raw.get("admin_profiles", {}), source, official_dir,
                     raw.get("remote_roots",[]),int(raw.get("max_sessions",4)),
                     int(raw.get("max_transfer_bytes",512*1024*1024)),int(raw.get("storage_budget_bytes",512*1024*1024)),
                     raw.get("downloads",{}),raw.get("intel",{}),auto)
        if not 1 <= result.max_sessions <= 16 or result.max_transfer_bytes < 1 or result.storage_budget_bytes < 1024*1024:
            raise ToolError("invalid_config")
        return result

    def credentials(self, peer_id: str, credential_ref: str | None, admin_ref: str | None, elevate: bool,
                    config_source: str | None = None):
        peer_id = "".join(peer_id.split())
        if not peer_id.isascii() or not peer_id.isdigit():
            raise ToolError("invalid_peer_id")
        source = config_source or self.config_source
        if source not in {"profile", "official"}:
            raise ToolError("invalid_config_source")
        result = {"peer_id": peer_id, "elevate": elevate, "config_source": source}
        if source == "official" and self.official_config_dir:
            result["official_config_dir"] = str(self.official_config_dir)
        if source == "profile" or credential_ref is not None:
            profile = self.profiles.get(credential_ref or "default")
            if not profile or "".join(str(profile.get("peer_id", "")).split()) != peer_id or not profile.get("password_dpapi"):
                raise ToolError("credential_unavailable")
            result["password"] = unprotect(profile["password_dpapi"])
            if not result["password"]:
                raise ToolError("credential_unavailable")
            if source == "profile":
                result.update(server=profile.get("server", ""), server_key=profile.get("server_key", ""),
                              force_relay=bool(profile.get("force_relay", False)))
        if elevate:
            admin = self.admin_profiles.get(admin_ref)
            if not admin or "".join(str(admin.get("peer_id", "")).split()) != peer_id:
                raise ToolError("credential_unavailable")
            result.update(os_username=admin["username"], os_password=unprotect(admin["password_dpapi"]))
        return result
