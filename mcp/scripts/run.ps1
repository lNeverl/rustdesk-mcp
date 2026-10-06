param([string]$Config='')
$ErrorActionPreference='Stop'
$package=(Resolve-Path "$PSScriptRoot/..").Path
$python=Join-Path $package 'runtime/Scripts/python.exe'
if(!(Test-Path -LiteralPath $python)){throw 'Run scripts/install.ps1 first.'}
$arguments=@('-m','rustdesk_mcp.app','--bridge-exe',(Join-Path $package 'rustdesk-mcp-bridge.exe'))
if($Config){$arguments+=@('--config',[IO.Path]::GetFullPath($Config))}
& $python @arguments
exit $LASTEXITCODE
