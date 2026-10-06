param([Parameter(Mandatory)][string]$PackagePath,[Parameter(Mandatory)][string]$ExpectedSHA256)
$ErrorActionPreference='Stop'
if($PackagePath -notmatch '^[A-Za-z]:[\\/]' -or $PackagePath -match '["\r\n]' -or [IO.Path]::GetExtension($PackagePath) -ne '.msi'){throw 'invalid_msi_path'}
if((Get-FileHash -LiteralPath $PackagePath -Algorithm SHA256).Hash -ne $ExpectedSHA256){throw 'package_hash_conflict'}
$result=Start-Process -FilePath "$env:SystemRoot\System32\msiexec.exe" -ArgumentList @('/i',('"'+$PackagePath+'"'),'/qn','/norestart','REBOOT=ReallySuppress') -Wait -PassThru
@{package=$PackagePath;exit_code=$result.ExitCode;reboot_required=$result.ExitCode -in @(3010,1641)}|ConvertTo-Json -Compress
if($result.ExitCode -notin @(0,3010,1641)){throw 'installer_failed'}
