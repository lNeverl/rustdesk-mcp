param([string]$PackageDir='', [string]$KimiConfig='', [string]$PythonExe='', [switch]$AutoApprove)
$ErrorActionPreference='Stop'
if(!$PackageDir){
    $packageRoot=(Resolve-Path "$PSScriptRoot/..").Path
    if(Test-Path -LiteralPath "$packageRoot/manifest.json"){$PackageDir=$packageRoot}
    else{
        $workspace=(Resolve-Path "$PSScriptRoot/../..").Path
        $version=[regex]::Match((Get-Content -LiteralPath "$workspace/mcp/server/pyproject.toml" -Raw),'(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
        $rustdeskVersion=[regex]::Match((Get-Content -LiteralPath "$workspace/Cargo.toml" -Raw),'(?m)^version\s*=\s*"([^"]+)"').Groups[1].Value
        $PackageDir=Join-Path $workspace "build/mcp-dist/$version-rustdesk-$rustdeskVersion"
    }
}
$PackageDir=(Resolve-Path -LiteralPath $PackageDir).Path
if(!$KimiConfig){$KimiConfig=Join-Path $env:USERPROFILE '.kimi-code/mcp.json'}
$KimiConfig=[IO.Path]::GetFullPath($KimiConfig)
$original=if(Test-Path -LiteralPath $KimiConfig){[IO.File]::ReadAllText($KimiConfig)}else{$null}
$client=if($null -ne $original){$original|ConvertFrom-Json}else{[pscustomobject]@{}}
if(!$client.mcpServers){$client|Add-Member -NotePropertyName mcpServers -NotePropertyValue ([pscustomobject]@{}) -Force}
$manifest=Get-Content -LiteralPath "$PackageDir/manifest.json" -Raw|ConvertFrom-Json
if($manifest.artifact -ne 'rustdesk-mcp-bridge.exe' -or $manifest.python_artifact -notmatch '^rustdesk_mcp_server-[0-9]+\.[0-9]+\.[0-9]+-py3-none-any\.whl$'){
    throw 'Unexpected package artifact names.'
}
$members=@($manifest.artifact,$manifest.python_artifact,'manifest.json','runtime-requirements.txt','README.md','LICENCE','pyproject.toml','uv.lock','scripts','docs')
foreach($member in $members){if(!(Test-Path -LiteralPath (Join-Path $PackageDir $member))){throw "Package incomplete: $member. Run package.ps1 first."}}
$stamp=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
$releaseId="$stamp-$((Get-FileHash -LiteralPath "$PackageDir/manifest.json" -Algorithm SHA256).Hash.Substring(0,12).ToLowerInvariant())"
$releaseRoot=[IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'RustDeskMCP/releases'))
$destination=Join-Path $releaseRoot $releaseId
New-Item -ItemType Directory -Path $destination -Force|Out-Null
foreach($member in $members){Copy-Item -LiteralPath (Join-Path $PackageDir $member) -Destination $destination -Recurse}
$shell=(Get-Process -Id $PID).Path
$installArgs=@('-NoProfile','-File',"$destination/scripts/install.ps1")
if($PythonExe){$installArgs+=@('-PythonExe',$PythonExe)}
if($AutoApprove){$installArgs+='-AutoApprove'}
& $shell @installArgs
if($LASTEXITCODE){throw 'Installation failed; Kimi configuration was not changed.'}
$runtimePython=Join-Path $destination 'runtime/Scripts/python.exe'
$bridge=Join-Path $destination $manifest.artifact
& $runtimePython -X utf8 "$destination/scripts/check_bridge.py" --bridge-exe $bridge
if($LASTEXITCODE){throw 'Native acceptance failed; Kimi configuration was not changed.'}
$validationData=Join-Path $destination 'validation-data'
try{
    & $runtimePython -X utf8 "$destination/scripts/smoke.py" --bridge-exe $bridge --data-dir $validationData
    if($LASTEXITCODE){throw 'MCP acceptance failed; Kimi configuration was not changed.'}
}finally{
    $validationPath=[IO.Path]::GetFullPath($validationData)
    if(!$validationPath.StartsWith($destination+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)){
        throw 'Validation cleanup escaped the release directory.'
    }
    if(Test-Path -LiteralPath $validationPath){Remove-Item -LiteralPath $validationPath -Recurse -Force}
}
$registration=(Get-Content -LiteralPath "$destination/client-config.json" -Raw|ConvertFrom-Json).mcpServers.rustdesk
$existing=$client.mcpServers.rustdesk
if($existing){
    $existing|Add-Member -NotePropertyName command -NotePropertyValue $registration.command -Force
    $existing|Add-Member -NotePropertyName args -NotePropertyValue $registration.args -Force
    if($AutoApprove){
        if(!$existing.env){$existing|Add-Member -NotePropertyName env -NotePropertyValue ([pscustomobject]@{}) -Force}
        $existing.env|Add-Member -NotePropertyName RUSTDESK_MCP_AUTO_APPROVE -NotePropertyValue '1' -Force
    }
}else{$client.mcpServers|Add-Member -NotePropertyName rustdesk -NotePropertyValue $registration}
$current=if(Test-Path -LiteralPath $KimiConfig){[IO.File]::ReadAllText($KimiConfig)}else{$null}
if($current -cne $original){throw 'Kimi configuration changed during deployment; retry after reviewing it.'}
New-Item -ItemType Directory -Path (Split-Path -Parent $KimiConfig) -Force|Out-Null
$backup=if($null -ne $original){"$KimiConfig.bak-$releaseId"}else{$null}
$temporary="$KimiConfig.new-$releaseId"
[IO.File]::WriteAllText($temporary,($client|ConvertTo-Json -Depth 64),[Text.UTF8Encoding]::new($false))
if($backup){[IO.File]::Replace($temporary,$KimiConfig,$backup)}else{[IO.File]::Move($temporary,$KimiConfig)}
@{release_id=$releaseId;source_revision=$manifest.source_revision;bridge_version=$manifest.bridge_version;
  python_version=$manifest.python_version;bridge_sha256=$manifest.sha256;python_sha256=$manifest.python_sha256;
  kimi_config=$KimiConfig;config_backup=$backup;runtime_python=$runtimePython;bridge_exe=$bridge;
  deployed_at=[DateTime]::UtcNow.ToString('o')}|ConvertTo-Json -Depth 4|
    Set-Content -LiteralPath "$destination/deployment.json" -Encoding utf8
Write-Host "Deployed: $destination"
Write-Host "Kimi configuration: $KimiConfig"
Write-Host "Configuration backup: $backup"
Write-Host 'Fully quit and restart Kimi to load this release.'
