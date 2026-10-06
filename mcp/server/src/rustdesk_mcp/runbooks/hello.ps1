param([string]$Message='RustDesk MCP')
$ErrorActionPreference='Stop'
@{message=$Message;cwd=$PWD.Path;identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name;version=$PSVersionTable.PSVersion.ToString()}|ConvertTo-Json -Compress
