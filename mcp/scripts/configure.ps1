param(
    [ValidateSet('profile','official')][string]$ConfigSource = 'profile',
    [string]$OfficialConfigDir = '',
    [string]$PeerId = '',
    [string]$Server = '',
    [string]$ServerKey = '',
    [string]$CredentialRef = 'test-peer',
    [Security.SecureString]$Password,
    [string]$AdminPeerId = '',
    [string]$AdminCredentialRef = 'admin',
    [string]$AdminUsername = '',
    [Security.SecureString]$AdminPassword,
    [string]$BridgeExe = '',
    [switch]$AllowWrites
)
$ErrorActionPreference = 'Stop'
if (!$BridgeExe) {
    $packagedBridge = Join-Path $PSScriptRoot '../rustdesk-mcp-bridge.exe'
    $BridgeExe = if (Test-Path -LiteralPath $packagedBridge) { [IO.Path]::GetFullPath($packagedBridge) } else {
        $workspace = (Resolve-Path "$PSScriptRoot/../..").Path
        $rustdeskVersion=[regex]::Match((Get-Content -LiteralPath "$workspace/Cargo.toml" -Raw),'(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
        $pythonVersion=[regex]::Match((Get-Content -LiteralPath "$workspace/mcp/server/pyproject.toml" -Raw),'(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
        Join-Path $workspace "build/mcp-dist/$pythonVersion-rustdesk-$rustdeskVersion/rustdesk-mcp-bridge.exe"
    }
}
if ($ConfigSource -eq 'profile' -and !$PeerId) { throw 'Profile source requires -PeerId' }
if ($PeerId) {
    if (($PeerId -replace '\s', '') -notmatch '^[0-9]+$') { throw 'Invalid peer ID' }
    if (!$Password) { $Password = Read-Host 'RustDesk peer password' -AsSecureString }
    Add-Type -AssemblyName System.Security
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password)
    try {
        $plaintext = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
        $bytes = [Text.Encoding]::UTF8.GetBytes($plaintext)
        $protected = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
        if ($bytes) { [Array]::Clear($bytes, 0, $bytes.Length) }
        $plaintext = $null
    }
}
$dataRoot = Join-Path $env:LOCALAPPDATA 'RustDeskMCP'
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
icacls $dataRoot /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE) { throw 'Unable to restrict configuration directory' }
$configPath = Join-Path $dataRoot 'config.json'
$config = @{}
if (Test-Path $configPath) {
    $oldConfig = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    foreach ($property in $oldConfig.PSObject.Properties) { $config[$property.Name] = $property.Value }
}
$profiles = @{}
if ($config.profiles) {
    foreach ($property in $config.profiles.PSObject.Properties) { $profiles[$property.Name] = $property.Value }
}
$config.profiles = $profiles
if ($AdminPeerId) {
    if (($AdminPeerId -replace '\s','') -notmatch '^[0-9]+$' -or !$AdminUsername) { throw 'Administrator profile requires valid AdminPeerId and AdminUsername' }
    if (!$AdminPassword) { $AdminPassword = Read-Host 'Remote Windows administrator password' -AsSecureString }
    Add-Type -AssemblyName System.Security
    $adminBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($AdminPassword)
    try {
        $adminBytes = [Text.Encoding]::UTF8.GetBytes([Runtime.InteropServices.Marshal]::PtrToStringBSTR($adminBstr))
        $adminProtected = [Security.Cryptography.ProtectedData]::Protect($adminBytes,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($adminBstr)
        if ($adminBytes) { [Array]::Clear($adminBytes,0,$adminBytes.Length) }
    }
    $adminProfiles = @{}
    if ($config.admin_profiles) { foreach ($property in $config.admin_profiles.PSObject.Properties) { $adminProfiles[$property.Name] = $property.Value } }
    $adminProfiles[$AdminCredentialRef] = @{peer_id=($AdminPeerId -replace '\s','');username=$AdminUsername;password_dpapi=[Convert]::ToBase64String($adminProtected)}
    $config.admin_profiles = $adminProfiles
}
$config.bridge_exe = [IO.Path]::GetFullPath($BridgeExe)
$config.allow_writes = [bool]$AllowWrites
if ($PeerId) {
    $config.profiles[$CredentialRef] = @{
        peer_id = ($PeerId -replace '\s', '')
        server = $Server
        server_key = $ServerKey
        password_dpapi = [Convert]::ToBase64String($protected)
    }
}
$config.config_source = $ConfigSource
if ($OfficialConfigDir) { $config.official_config_dir = (Resolve-Path -LiteralPath $OfficialConfigDir).Path }
$config | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $configPath -Encoding utf8
Write-Host "Saved configuration source '$ConfigSource' to $configPath"
if ($PeerId) { Write-Host "Saved protected credential reference '$CredentialRef'" }
