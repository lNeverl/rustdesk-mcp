param([ValidateSet('debug','release')][string]$Profile = 'release', [switch]$FeatureOff)
$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path "$PSScriptRoot/../..").Path
$rustdeskVersion = [regex]::Match((Get-Content -LiteralPath "$workspace/Cargo.toml" -Raw),'(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
$bridgeVersion = [regex]::Match((Get-Content -LiteralPath "$workspace/src/mcp_bridge/mod.rs" -Raw),'BRIDGE_VERSION: &str = "([^"]+)"').Groups[1].Value
$pythonVersion = [regex]::Match((Get-Content -LiteralPath "$workspace/mcp/server/pyproject.toml" -Raw),'(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
if (!$rustdeskVersion -or !$bridgeVersion -or $pythonVersion -ne $bridgeVersion) { throw 'Bridge/Python versions must match; source version metadata is invalid.' }
$toolsRoot = Join-Path $workspace 'build/mcp-tools'
$env:CARGO_HOME = Join-Path $workspace 'build/c'
$env:RUSTUP_HOME = Join-Path $toolsRoot 'rustup'
$env:CARGO_TARGET_DIR = Join-Path $workspace 'target'
$env:VCPKG_ROOT = Join-Path $toolsRoot 'vcpkg'
$env:LIBCLANG_PATH = Join-Path $toolsRoot 'python/clang/native'
$env:TEMP = Join-Path $toolsRoot 'tmp'
$env:TMP = $env:TEMP
$env:PATH = "$env:CARGO_HOME/bin;$(Join-Path $toolsRoot 'python/cmake/data/bin');$env:PATH"
$env:CARGO_BUILD_JOBS = [string][Math]::Min(8, [Environment]::ProcessorCount)
$gitConfigPath = Join-Path $toolsRoot 'gitconfig'
$existingGitConfig = if ($env:GIT_CONFIG_GLOBAL) { $env:GIT_CONFIG_GLOBAL } else { Join-Path $env:USERPROFILE '.gitconfig' }
$gitConfig = @('[core]', 'longpaths = true')
if (Test-Path -LiteralPath $existingGitConfig) {
    $gitConfig = @('[include]', ('path = "' + $existingGitConfig.Replace('\','/').Replace('"','\"') + '"')) + $gitConfig
}
$gitConfig | Set-Content -LiteralPath $gitConfigPath -Encoding utf8
$env:GIT_CONFIG_GLOBAL = $gitConfigPath
$env:GIT_TERMINAL_PROMPT = '0'
$env:GCM_INTERACTIVE = 'never'
$revision = git -C $workspace rev-parse HEAD
if ($LASTEXITCODE -or !$revision) { throw 'git revision unavailable' }
$revision = $revision.Trim()
if (git -C $workspace status --porcelain) { $revision += '-dirty' }
$env:RUSTDESK_MCP_REVISION = $revision
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio/Installer/vswhere.exe'
$vsInstall = (& $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath).Trim()
if (!$vsInstall) { throw 'Existing Visual Studio C++ tools were not found. This script does not install system components.' }
& (Join-Path $vsInstall 'Common7/Tools/Launch-VsDevShell.ps1') -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | Out-Null
$env:VCPKG_ROOT = Join-Path $toolsRoot 'vcpkg'
$env:VCPKG_INSTALLED_ROOT = Join-Path $env:VCPKG_ROOT 'installed'
$env:LIBCLANG_PATH = Join-Path $toolsRoot 'python/clang/native'
Push-Location $workspace
try {
    if ($FeatureOff) {
        & cargo +stable check --locked --lib
    } else {
        $cargoArgs = @('+stable', 'build', '--locked', '--bin', 'rustdesk-mcp-bridge', '--features', 'mcp-bridge')
        if ($Profile -eq 'release') { $cargoArgs += '--release' }
        & cargo @cargoArgs
    }
    if ($LASTEXITCODE) { throw 'Cargo build/check failed' }
    if (!$FeatureOff) {
        $targetProfile = if ($Profile -eq 'debug') { 'debug' } else { 'release' }
        $destination = Join-Path $workspace "build/mcp-dist/$bridgeVersion-rustdesk-$rustdeskVersion"
        New-Item -ItemType Directory -Force -Path $destination | Out-Null
        Copy-Item -LiteralPath "$env:CARGO_TARGET_DIR/$targetProfile/rustdesk-mcp-bridge.exe" -Destination $destination
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath "$destination/rustdesk-mcp-bridge.exe").Hash
        & (Get-Process -Id $PID).Path -NoProfile -File "$PSScriptRoot/dev.ps1" uv build "$workspace/mcp/server" --wheel --out-dir $destination
        if ($LASTEXITCODE) { throw 'Python MCP wheel build failed' }
        $pythonArtifact = "rustdesk_mcp_server-$pythonVersion-py3-none-any.whl"
        $pythonHash = (Get-FileHash -Algorithm SHA256 -LiteralPath "$destination/$pythonArtifact").Hash
        @{product='RustDesk MCP Bridge'; bridge_version=$bridgeVersion; python_version=$pythonVersion;
          protocol=@{major=1;minor=1}; rustdesk_version=$rustdeskVersion; source_revision=$revision;
          artifact='rustdesk-mcp-bridge.exe'; sha256=$hash; profile=$Profile; architecture='x64';
          python_artifact=$pythonArtifact; python_sha256=$pythonHash; rustc_version=(& rustc +stable --version).Trim();
          built_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json -Depth 4 |
            Set-Content -LiteralPath "$destination/manifest.json" -Encoding utf8
        Get-FileHash -Algorithm SHA256 -LiteralPath "$destination/rustdesk-mcp-bridge.exe" | Select-Object Hash,Path
    }
} finally { Pop-Location }
