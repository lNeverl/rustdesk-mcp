param([string]$Version='')
$ErrorActionPreference='Stop'
$workspace=(Resolve-Path "$PSScriptRoot/../..").Path
$rustdeskVersion=[regex]::Match((Get-Content -LiteralPath "$workspace/Cargo.toml" -Raw),'(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
$pythonVersion=[regex]::Match((Get-Content -LiteralPath "$workspace/mcp/server/pyproject.toml" -Raw),'(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
if(!$Version){$Version=$pythonVersion}
if($Version -ne $pythonVersion){throw 'Package version must match the Python source.'}
$destination=Join-Path $workspace "build/mcp-dist/$Version-rustdesk-$rustdeskVersion"
if(!(Test-Path -LiteralPath "$destination/rustdesk-mcp-bridge.exe")){throw 'Build the bridge first.'}
$shell=(Get-Process -Id $PID).Path
& $shell -NoProfile -File "$PSScriptRoot/dev.ps1" uv build "$workspace/mcp/server" --wheel --out-dir $destination
if($LASTEXITCODE){throw 'Wheel build failed.'}
& $shell -NoProfile -File "$PSScriptRoot/dev.ps1" uv export --project "$workspace/mcp/server" --frozen --no-emit-project --format requirements-txt --output-file "$destination/runtime-requirements.txt"
if($LASTEXITCODE){throw 'Runtime lock export failed.'}
$manifest=Get-Content -LiteralPath "$destination/manifest.json" -Raw|ConvertFrom-Json
$manifest.python_sha256=(Get-FileHash -LiteralPath (Join-Path $destination $manifest.python_artifact) -Algorithm SHA256).Hash
$manifest|ConvertTo-Json -Depth 5|Set-Content -LiteralPath "$destination/manifest.json" -Encoding utf8
New-Item -ItemType Directory -Force -Path "$destination/scripts","$destination/docs"|Out-Null
Copy-Item -LiteralPath "$PSScriptRoot/install.ps1","$PSScriptRoot/run.ps1","$PSScriptRoot/configure.ps1","$PSScriptRoot/smoke.py","$PSScriptRoot/check_bridge.py" -Destination "$destination/scripts"
Copy-Item -Path "$workspace/mcp/docs/*.md" -Destination "$destination/docs"
Copy-Item -LiteralPath "$workspace/mcp/docs/tools-schema.json" -Destination "$destination/docs"
Copy-Item -LiteralPath "$workspace/mcp/README.md","$workspace/LICENCE","$workspace/mcp/server/pyproject.toml","$workspace/mcp/server/uv.lock" -Destination $destination
$zip=Join-Path $workspace "build/mcp-dist/rustdesk-mcp-$Version-windows-x64.zip"
# Explicit package members exclude runtime caches, credentials and earlier test artifacts.
Compress-Archive -LiteralPath "$destination/rustdesk-mcp-bridge.exe","$destination/$($manifest.python_artifact)","$destination/manifest.json","$destination/runtime-requirements.txt","$destination/README.md","$destination/LICENCE","$destination/pyproject.toml","$destination/uv.lock","$destination/scripts","$destination/docs" -DestinationPath $zip -Force
Get-FileHash -LiteralPath $zip -Algorithm SHA256|Select-Object Hash,Path
