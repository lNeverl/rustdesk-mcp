import base64
import codecs
import json

from .errors import ToolError

SYSTEM_INFO = r"""
if (Get-Module PSReadLine) { Remove-Module PSReadLine -ErrorAction Stop }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$os = Get-CimInstance Win32_OperatingSystem
$tools = @{}
foreach ($name in @('git','python','uv','rg','pwsh','powershell','cargo','node','dotnet','cmake')) {
    $tool = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
    $tools[$name] = $(if ($tool) { $tool.Source } else { $null })
}
@{os=$os.Caption;build=$os.BuildNumber;os_architecture=$os.OSArchitecture;
  shell='powershell';shell_version=$PSVersionTable.PSVersion.ToString();
  process_bits=[IntPtr]::Size*8;identity=$identity.Name;sid=$identity.User.Value;
  administrator=$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);
  cwd=$PWD.Path;tools=$tools} | ConvertTo-Json -Depth 5 -Compress
"""

DIAGNOSTICS = {
    "diagnostic:system_info": SYSTEM_INFO,
    "diagnostic:processes": "Get-Process | Select-Object -First 100 Id,ProcessName,CPU,WorkingSet64 | ConvertTo-Json -Compress",
    "diagnostic:services": "Get-Service | Select-Object -First 100 Name,DisplayName,Status | ConvertTo-Json -Compress",
    "diagnostic:network": "Get-NetTCPConnection | Select-Object -First 100 LocalAddress,LocalPort,RemoteAddress,RemotePort,State,OwningProcess | ConvertTo-Json -Compress",
    "diagnostic:disk": "Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID,Size,FreeSpace,FileSystem | ConvertTo-Json -Compress",
    "diagnostic:unicode": "Write-Output 'RustDesk MCP 中文输出验证 😀'",
}


def encoded_script(script: str) -> str:
    return base64.b64encode(script.encode("utf-8")).decode("ascii")


def wrapper(script: str, nonce: str, cwd: str | None = None, native_output_encoding: str = "utf8") -> str:
    if len(script.encode("utf-8")) > 64 * 1024:
        raise ToolError("command_too_large")
    if native_output_encoding not in {"utf8", "gbk"}:
        raise ToolError("unsupported_native_output_encoding")
    native_setup = native_reset = ""
    if native_output_encoding == "gbk":
        native_setup = "[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(936);"
        native_reset = "[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false); $OutputEncoding = [Console]::OutputEncoding;"
    location = ""
    if cwd is not None:
        encoded_cwd = encoded_script(cwd)
        location = f"Set-Location -LiteralPath ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_cwd}'))) -ErrorAction Stop; if ($PWD.Provider.Name -ne 'FileSystem') {{ throw 'filesystem_cwd_required' }};"
    # All markers are inside the encoded script; the PTY input echo cannot look like a result.
    payload = f"""
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$env:UV_PYTHON_DOWNLOADS = 'never'
[Console]::WriteLine('')
[Console]::WriteLine('__RDMCP_{nonce}_BEGIN__')
$__rd_failed = $false
$global:LASTEXITCODE = 0
$script:__rd_sequence = 0
function __rd_frame([string]$kind, [string]$value) {{
  $__rd_encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($value))
  for ($__rd_at = 0; $__rd_at -lt $__rd_encoded.Length; $__rd_at += 64) {{
    [Console]::WriteLine('__RDMCP_{nonce}_' + $kind + '__:' + $script:__rd_sequence + ':' + $__rd_encoded.Substring($__rd_at, [Math]::Min(64, $__rd_encoded.Length - $__rd_at)))
    $script:__rd_sequence++
    if ($script:__rd_sequence % 8 -eq 0) {{ [Threading.Thread]::Sleep(2) }}
  }}
}}
try {{
  {location}
  {native_setup}
  . ([ScriptBlock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded_script(script)}')))) 2>&1 |
    ForEach-Object {{
      if ($_ -is [Management.Automation.ErrorRecord] -and $_.FullyQualifiedErrorId -notlike 'NativeCommandError*') {{ $script:__rd_failed = $true }}
      $_
    }} | Out-String -Stream | ForEach-Object {{ __rd_frame 'DATA' ([string]$_ + "`n") }}
  $__rd_ok = $?
  if (-not $__rd_ok) {{ $__rd_failed = $true }}
}} catch {{ $__rd_failed = $true; __rd_frame 'DATA' ([string]$_ + "`n") }}
{native_reset}
$__rd_native = $global:LASTEXITCODE
$__rd_result = @{{powershell_success=(-not $__rd_failed);native_exit_code=$__rd_native;cwd=$PWD.Path;shell_version=$PSVersionTable.PSVersion.ToString()}} | ConvertTo-Json -Compress
__rd_frame 'META' $__rd_result
[Console]::WriteLine('__RDMCP_{nonce}_END__')
"""
    encoded = encoded_script(payload)
    if len(encoded) <= 4096:
        return f". ([ScriptBlock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))))\r"
    # Bounded input lines avoid quadratic redraw output from the remote line editor.
    chunks = [encoded[at:at+2048] for at in range(0,len(encoded),2048)]
    return ("$__rd_payload=''\r" + ''.join(f"$__rd_payload+='{chunk}'\r" for chunk in chunks) +
            ". ([ScriptBlock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($__rd_payload))))\r")


class Parser:
    def __init__(self, nonce: str):
        self.begin = f"__RDMCP_{nonce}_BEGIN__"
        self.end = f"__RDMCP_{nonce}_END__"
        self.data = f"__RDMCP_{nonce}_DATA__:"
        self.meta = f"__RDMCP_{nonce}_META__:"
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.payload_decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.metadata = bytearray()
        self.started = False
        self.finished = False
        self.buffer = ""
        self.escape = "normal"
        self.next_sequence = 0

    def record(self, value):
        number, encoded = value.split(':',1)
        if int(number) != self.next_sequence:
            raise ValueError()
        self.next_sequence += 1
        return base64.b64decode(encoded,validate=True)

    def _plain(self, text: str) -> str:
        result = []
        for char in text:
            if self.escape == "esc":
                self.escape = "csi" if char == "[" else "osc" if char == "]" else "normal"
            elif self.escape == "csi":
                if "@" <= char <= "~":
                    self.escape = "normal"
            elif self.escape == "osc":
                if char == "\a":
                    self.escape = "normal"
                elif char == "\x1b":
                    self.escape = "osc_end"
            elif self.escape == "osc_end":
                self.escape = "normal" if char == "\\" else "osc"
            elif char == "\x1b":
                self.escape = "esc"
            elif char != "\r":
                result.append(char)
        return "".join(result)

    def feed(self, data: bytes) -> tuple[str, dict | None]:
        if self.finished:
            return "", None
        try:
            self.buffer += self._plain(self.decoder.decode(data))
        except UnicodeError:
            raise ToolError("stream_lost") from None
        if not self.started:
            position = self.buffer.find(self.begin)
            if position < 0:
                self.buffer = self.buffer[-len(self.begin):]
                return "", None
            self.buffer = self.buffer[position + len(self.begin):]
            self.started = True
        output = []
        try:
            while "\n" in self.buffer:
                line, self.buffer = self.buffer.split("\n", 1)
                if line.startswith(self.data):
                    payload = self.record(line[len(self.data):])
                    output.append(self.payload_decoder.decode(payload))
                elif line.startswith(self.meta):
                    self.metadata.extend(self.record(line[len(self.meta):]))
                    if len(self.metadata) > 8192:
                        raise ValueError()
                elif line == self.end:
                    output.append(self.payload_decoder.decode(b"", final=True))
                    metadata = json.loads(self.metadata)
                    if not isinstance(metadata["powershell_success"], bool) or not isinstance(metadata["native_exit_code"], int):
                        raise ValueError()
                    self.finished = True
                    return "".join(output), metadata
                elif line:
                    output.append(line + "\n")
            if len(self.buffer) > 8192:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ToolError("stream_lost") from None
        return "".join(output), None
