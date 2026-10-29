$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path "$PSScriptRoot/../..").Path
$env:UV_CACHE_DIR = Join-Path $workspace 'build/mcp-tools/uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $workspace 'build/mcp-tools/uv-python'
$env:UV_PYTHON_DOWNLOADS = 'never'
$env:PYTHONUTF8 = '1'
$pythonCommand = Get-Command python -ErrorAction Stop
& uv run --project "$workspace/mcp/server" --python $pythonCommand.Source @args
exit $LASTEXITCODE
