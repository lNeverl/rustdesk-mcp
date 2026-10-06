param([string]$PythonExe='', [switch]$Configure, [switch]$AllowWrites)
$ErrorActionPreference='Stop'
$package=(Resolve-Path "$PSScriptRoot/..").Path
if(!(Test-Path -LiteralPath "$package/manifest.json")){throw 'Run this script from the extracted delivery package scripts directory.'}
$manifest=Get-Content -LiteralPath "$package/manifest.json" -Raw|ConvertFrom-Json
foreach($entry in @(@{name=$manifest.artifact;hash=$manifest.sha256},@{name=$manifest.python_artifact;hash=$manifest.python_sha256})){
    if((Get-FileHash -LiteralPath (Join-Path $package $entry.name) -Algorithm SHA256).Hash -ne $entry.hash){throw "Artifact hash mismatch: $($entry.name)"}
}
if(!$PythonExe){$PythonExe=(Get-Command python -ErrorAction Stop).Source}
$runtime=Join-Path $package 'runtime'
if(Test-Path -LiteralPath $runtime){throw 'Runtime already exists. Use a new version directory for install/upgrade.'}
& $PythonExe -m venv $runtime
if($LASTEXITCODE){throw 'Python 3.11 or later with venv is required.'}
$runtimePython=Join-Path $runtime 'Scripts/python.exe'
$env:UV_CACHE_DIR=Join-Path $package 'cache/uv'
$env:UV_PYTHON_DOWNLOADS='never'
& uv pip sync --python $runtimePython --require-hashes "$package/runtime-requirements.txt"
if($LASTEXITCODE){throw 'Runtime dependency installation failed.'}
& uv pip install --python $runtimePython --no-deps (Join-Path $package $manifest.python_artifact)
if($LASTEXITCODE){throw 'MCP wheel installation failed.'}
$bridge=Join-Path $package $manifest.artifact
@{mcpServers=@{rustdesk=@{command=$runtimePython;args=@('-m','rustdesk_mcp.app','--bridge-exe',$bridge)}}}|
    ConvertTo-Json -Depth 6|Set-Content -LiteralPath "$package/client-config.json" -Encoding utf8
if($Configure){& "$PSScriptRoot/configure.ps1" -ConfigSource official -BridgeExe $bridge -AllowWrites:$AllowWrites}
Write-Host "Installed isolated runtime: $runtime"
Write-Host "Client registration example: $package/client-config.json"
