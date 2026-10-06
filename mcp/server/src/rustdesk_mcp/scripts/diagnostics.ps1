$ErrorActionPreference='Stop'
function Fail([string]$code){throw "RDMCP:$code"}
function Page($items){$all=@($items);return @{items=@($all|Select-Object -Skip $rd.offset -First $rd.limit);offset=$rd.offset;next_offset=$rd.offset+[Math]::Min([Math]::Max(0,$all.Count-$rd.offset),$rd.limit);has_more=$rd.offset+$rd.limit -lt $all.Count;total=$all.Count}}
try {
    $data=switch($rd.kind){
        'list_processes' {
            $items=Get-CimInstance Win32_Process|Sort-Object ProcessId|ForEach-Object {
                @{pid=$_.ProcessId;parent_pid=$_.ParentProcessId;name=$_.Name;path=$_.ExecutablePath;
                  command_line=$_.CommandLine;creation_time=$(if($_.CreationDate){$_.CreationDate.ToUniversalTime().ToString('o')}else{$null});
                  working_set_bytes=$_.WorkingSetSize;kernel_time=$_.KernelModeTime;user_time=$_.UserModeTime;
                  unavailable=@($(if(!$_.ExecutablePath){'path'}),$(if(!$_.CommandLine){'command_line'}))}
            };Page $items
        }
        'list_services' {Page @(Get-CimInstance Win32_Service|Sort-Object Name|ForEach-Object {@{name=$_.Name;display_name=$_.DisplayName;state=$_.State;start_mode=$_.StartMode;account=$_.StartName;path=$_.PathName;pid=$_.ProcessId}})}
        'list_installed_software' {
            $items=@();$covered=@();$unavailable=@()
            foreach($root in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall')){
                try{$items+=@(Get-ItemProperty -Path ($root+'\*') -ErrorAction Stop|Where-Object DisplayName|ForEach-Object {@{name=$_.DisplayName;version=$_.DisplayVersion;publisher=$_.Publisher;install_location=$_.InstallLocation;scope=$root;registry_key=$_.PSPath}});$covered+=$root}catch{$unavailable+=$root}
            }
            $p=Page ($items|Sort-Object name);$p.covered=$covered;$p.unavailable=$unavailable;$p.portable_apps_included=$false;$p
        }
        'get_startup_items' {
            $items=@();$covered=@();$unavailable=@()
            foreach($root in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run','HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run')){
                try{$key=Get-Item -LiteralPath $root;foreach($name in $key.GetValueNames()){$items+=@{source='registry';location=$root;name=$name;command=[string]$key.GetValue($name)}};$covered+=$root}catch{$unavailable+=$root}
            }
            foreach($folder in @([Environment]::GetFolderPath('Startup'),[Environment]::GetFolderPath('CommonStartup'))){
                try{$items+=@(Get-ChildItem -LiteralPath $folder -Force|ForEach-Object {@{source='startup_folder';name=$_.Name;path=$_.FullName}});$covered+=$folder}catch{$unavailable+=$folder}
            }
            try{$items+=@(Get-CimInstance Win32_Service -Filter "StartMode='Auto'"|ForEach-Object {@{source='service';name=$_.Name;command=$_.PathName;account=$_.StartName}});$covered+='automatic_services'}catch{$unavailable+='automatic_services'}
            try{$items+=@(Get-ScheduledTask|ForEach-Object {@{source='scheduled_task';name=$_.TaskName;path=$_.TaskPath;state=$_.State.ToString();actions=@($_.Actions|ForEach-Object {@{execute=$_.Execute;arguments=$_.Arguments}})}});$covered+='scheduled_tasks'}catch{$unavailable+='scheduled_tasks'}
            $p=Page $items;$p.covered=$covered;$p.unavailable=$unavailable;$p
        }
        'get_network_connections' {
            $items=@();$unavailable=@()
            try{$items+=@(Get-NetTCPConnection|Sort-Object LocalPort|ForEach-Object {@{protocol='tcp';local_address=$_.LocalAddress;local_port=$_.LocalPort;remote_address=$_.RemoteAddress;remote_port=$_.RemotePort;state=$_.State.ToString();pid=$_.OwningProcess}})}catch{$unavailable+='tcp'}
            try{$items+=@(Get-NetUDPEndpoint|Sort-Object LocalPort|ForEach-Object {@{protocol='udp';local_address=$_.LocalAddress;local_port=$_.LocalPort;pid=$_.OwningProcess}})}catch{$unavailable+='udp'}
            $p=Page $items;$p.unavailable=$unavailable;$p
        }
        'get_recent_events' {
            $filter=@{LogName=$rd.log_name;StartTime=(Get-Date).AddHours(-$rd.hours)}
            if($rd.provider){$filter.ProviderName=$rd.provider};if($rd.levels.Count){$filter.Level=$rd.levels}
            try{$items=@(Get-WinEvent -FilterHashtable $filter -MaxEvents $rd.limit -ErrorAction Stop|ForEach-Object {$message=[string]$_.Message;@{record_id=$_.RecordId;event_id=$_.Id;provider=$_.ProviderName;level=$_.Level;at=$_.TimeCreated.ToUniversalTime().ToString('o');message=$message.Substring(0,[Math]::Min($message.Length,4096));message_truncated=$message.Length -gt 4096;message_unavailable=!$message}})}catch {if($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*'){$items=@()}else{throw}}
            @{items=$items;log_name=$rd.log_name;hours=$rd.hours;limit=$rd.limit;limit_reached=$items.Count -eq $rd.limit}
        }
        'get_security_status' {
            if(!(Get-Command Get-MpComputerStatus -ErrorAction SilentlyContinue)){@{available=$false;reason='defender_cmdlet_unavailable'}}else{
                $s=Get-MpComputerStatus;@{available=$true;antivirus_enabled=$s.AntivirusEnabled;realtime_enabled=$s.RealTimeProtectionEnabled;service_enabled=$s.AMServiceEnabled;running_mode=$s.AMRunningMode;signature_version=$s.AntivirusSignatureVersion;signature_updated=$s.AntivirusSignatureLastUpdated;quick_scan_end=$s.QuickScanEndTime;full_scan_end=$s.FullScanEndTime;tamper_protected=$s.IsTamperProtected}
            }
        }
        'kill_process' {
            if($rd.pid -le 4 -or $rd.pid -eq $PID){Fail 'protected_process'}
            $p=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$rd.pid)
            if(!$p){Fail 'process_not_found'}
            if($p.Name -match '^(csrss|wininit|winlogon|lsass|services|smss|System|Registry|rustdesk.*)\.?(exe)?$'){Fail 'protected_process'}
            $handle=Get-Process -Id $rd.pid
            $created=$handle.StartTime.ToUniversalTime().ToString('o')
            if([Math]::Floor([DateTime]::Parse($rd.creation_time).ToUniversalTime().Ticks/10) -ne [Math]::Floor($handle.StartTime.ToUniversalTime().Ticks/10)){Fail 'process_identity_conflict'}
            $handle.Kill();$exited=$handle.WaitForExit(5000)
            @{pid=$rd.pid;creation_time=$created;exit_confirmed=$exited}
        }
        'control_service' {
            if($rd.name -match '^(RpcSs|DcomLaunch|WinDefend|WdNisSvc|SamSs|LSM|EventLog|Schedule|RustDesk.*)$'){Fail 'protected_service'}
            $s=Get-Service -Name $rd.name
            switch($rd.action){'start'{$s.Start()};'stop'{$s.Stop()};'restart'{$s.Stop();$s.WaitForStatus('Stopped',[TimeSpan]::FromSeconds(30));$s.Start()};default{Fail 'invalid_service_action'}}
            $target=$(if($rd.action -eq 'stop'){'Stopped'}else{'Running'});$s.WaitForStatus($target,[TimeSpan]::FromSeconds(30));$s.Refresh()
            @{name=$s.ServiceName;state=$s.Status.ToString();action=$rd.action;confirmed=$s.Status.ToString() -eq $target}
        }
        'run_defender_scan' {
            if(!(Get-Command Start-MpScan -ErrorAction SilentlyContinue)){Fail 'defender_unavailable'}
            if($rd.scan_type -eq 'custom' -and (!$rd.remote_path -or $rd.remote_path -notmatch '^[A-Za-z]:[\\/]')){Fail 'invalid_path'}
            $started=Get-Date
            if($rd.scan_type -eq 'custom'){Start-MpScan -ScanType CustomScan -ScanPath $rd.remote_path}else{Start-MpScan -ScanType ($rd.scan_type+'Scan')}
            $s=Get-MpComputerStatus
            $end=$(if($rd.scan_type -eq 'quick'){$s.QuickScanEndTime}elseif($rd.scan_type -eq 'full'){$s.FullScanEndTime}else{$null})
            @{scan_type=$rd.scan_type;submitted=$true;cmdlet_returned=$true;scan_completion_confirmed=[bool]($end -and $end -ge $started);scan_end=$end;detections=@(Get-MpThreatDetection|Select-Object -First 50|ForEach-Object {@{threat_id=$_.ThreatID;action_success=$_.ActionSuccess;detected_at=$_.InitialDetectionTime}})}
        }
        default {Fail 'unsupported_diagnostic'}
    }
    @{ok=$true;data=$data}|ConvertTo-Json -Depth 10 -Compress
} catch {
    $code=$(if($_.Exception.Message -match 'RDMCP:([a-z_]+)'){$Matches[1]}elseif($_.Exception -is [UnauthorizedAccessException]){'access_denied'}else{'diagnostic_failed'})
    @{ok=$false;error=@{code=$code;stage=$rd.kind}}|ConvertTo-Json -Compress
}
