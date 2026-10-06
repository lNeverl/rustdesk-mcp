param([Parameter(Mandatory)][string]$ProductCode)
$ErrorActionPreference='Stop'
if($ProductCode -notmatch '^\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}$'){throw 'invalid_product_code'}
$result=Start-Process -FilePath "$env:SystemRoot\System32\msiexec.exe" -ArgumentList @('/x',$ProductCode,'/qn','/norestart','REBOOT=ReallySuppress') -Wait -PassThru
@{product_code=$ProductCode;exit_code=$result.ExitCode;reboot_required=$result.ExitCode -in @(3010,1641)}|ConvertTo-Json -Compress
if($result.ExitCode -notin @(0,3010,1641)){throw 'uninstaller_failed'}
