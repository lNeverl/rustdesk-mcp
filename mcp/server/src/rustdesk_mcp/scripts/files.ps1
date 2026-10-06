$ErrorActionPreference = 'Stop'
function Fail([string]$code) { throw "RDMCP:$code" }
function Guard([string]$value) {
    if (!$value -or $value -notmatch '^[A-Za-z]:[\\/]' -or $value.Substring(2).Contains(':')) { Fail 'invalid_path' }
    $path = [IO.Path]::GetFullPath($value)
    if ($rd.roots.Count) {
        $permitted = $false
        foreach ($root in $rd.roots) {
            $base = [IO.Path]::GetFullPath($root).TrimEnd('\','/')
            if ($path.Equals($base,[StringComparison]::OrdinalIgnoreCase) -or $path.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)) { $permitted=$true }
        }
        if (!$permitted) { Fail 'path_outside_allowed_roots' }
    }
    $cursor = $path
    while ($cursor) {
        try {$attributes=[IO.File]::GetAttributes($cursor)} catch [IO.FileNotFoundException] {$attributes=0} catch [IO.DirectoryNotFoundException] {$attributes=0}
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { Fail 'reparse_path_refused' }
        $parent = [IO.Directory]::GetParent($cursor)
        $cursor = $(if ($parent) {$parent.FullName} else {$null})
    }
    return $path
}
function HashBytes([byte[]]$bytes) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() } finally {$sha.Dispose()}
}
function HashFile([string]$path) {
    $stream=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant() } finally {$sha.Dispose();$stream.Dispose()}
}
function Decode([byte[]]$bytes,[string]$requested) {
    $skip=0; $name=$requested
    if (!$name -or $name -eq 'auto' -or $name -eq 'preserve') {
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191) {$name='utf8';$skip=3}
        elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 255 -and $bytes[1] -eq 254) {$name='utf16le';$skip=2}
        elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 254 -and $bytes[1] -eq 255) {$name='utf16be';$skip=2}
        else {$name='utf8'}
    } else {
        if ($name -eq 'utf8' -and $bytes.Length -ge 3 -and $bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191) {$skip=3}
        if ($name -eq 'utf16le' -and $bytes.Length -ge 2 -and $bytes[0] -eq 255 -and $bytes[1] -eq 254) {$skip=2}
        if ($name -eq 'utf16be' -and $bytes.Length -ge 2 -and $bytes[0] -eq 254 -and $bytes[1] -eq 255) {$skip=2}
    }
    $encoding = switch ($name) {
        'utf8' {New-Object Text.UTF8Encoding($false,$true)}
        'utf16le' {New-Object Text.UnicodeEncoding($false,$true,$true)}
        'utf16be' {New-Object Text.UnicodeEncoding($true,$true,$true)}
        'gb18030' {[Text.Encoding]::GetEncoding(54936,[Text.EncoderFallback]::ExceptionFallback,[Text.DecoderFallback]::ExceptionFallback)}
        default {Fail 'unsupported_encoding'}
    }
    try {$text=$encoding.GetString($bytes,$skip,$bytes.Length-$skip)} catch {Fail 'binary_or_unknown_encoding'}
    if ($text.Contains([string][char]0)) {Fail 'binary_or_unknown_encoding'}
    return @{text=$text;encoding=$encoding;name=$name;bom=$skip}
}
function IndexAt([string]$text,[int]$points) {
    $at=0
    for ($i=0;$i -lt $points -and $at -lt $text.Length;$i++) {
        if ([char]::IsHighSurrogate($text[$at]) -and $at+1 -lt $text.Length -and [char]::IsLowSurrogate($text[$at+1])) {$at+=2} else {$at++}
    }
    return $at
}
function Points([string]$text) { return $text.Length-[regex]::Matches($text,'[\uD800-\uDBFF][\uDC00-\uDFFF]').Count }
function Info([string]$path) {
    $item=Get-Item -LiteralPath $path -Force
    return @{path=$item.FullName;name=$item.Name;directory=[bool]$item.PSIsContainer;size=$(if($item.PSIsContainer){$null}else{$item.Length});
        modified_at=$item.LastWriteTimeUtc.ToString('o');created_at=$item.CreationTimeUtc.ToString('o');attributes=$item.Attributes.ToString()}
}
function DeleteVerified([string]$path,[string]$expected) {
    if(!('RustDeskMcpFileDelete' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Microsoft.Win32.SafeHandles;
public static class RustDeskMcpFileDelete {
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)]
    static extern SafeFileHandle CreateFile(string p,uint a,uint s,IntPtr sa,uint c,uint f,IntPtr t);
    [DllImport("kernel32.dll",SetLastError=true)]
    static extern bool SetFileInformationByHandle(SafeFileHandle h,int c,ref int i,uint n);
    public static void Delete(string path,string expected) {
        using(var h=CreateFile(path,0x80010000,0,IntPtr.Zero,3,0x80,IntPtr.Zero)) {
            if(h.IsInvalid) throw new IOException("RDMCP:file_busy_or_io_error");
            using(var stream=new FileStream(h,FileAccess.Read)) using(var sha=SHA256.Create()) {
                var hash=BitConverter.ToString(sha.ComputeHash(stream)).Replace("-","").ToLowerInvariant();
                if(hash!=expected) throw new IOException("RDMCP:file_conflict");
                int delete=1;
                if(!SetFileInformationByHandle(h,4,ref delete,4)) throw new IOException("RDMCP:file_delete_failed");
            }
        }
    }
}
'@
    }
    [RustDeskMcpFileDelete]::Delete($path,$expected)
}
function Walk([string]$root) {
    $queue=New-Object 'Collections.Generic.Queue[object]';$queue.Enqueue(@{path=$root;depth=0})
    $watch=[Diagnostics.Stopwatch]::StartNew();$visited=0;$skipped=0;$limited=$false
    $found=New-Object 'Collections.Generic.List[object]'
    $regex=$null
    if ($rd.regex) {
        $options=[Text.RegularExpressions.RegexOptions]::CultureInvariant
        if(!$rd.case_sensitive){$options=$options -bor [Text.RegularExpressions.RegexOptions]::IgnoreCase}
        $regex=New-Object Text.RegularExpressions.Regex($rd.query,$options,[TimeSpan]::FromMilliseconds(200))
    }
    while ($queue.Count) {
        $folder=$queue.Dequeue()
        try {$entries=Get-ChildItem -LiteralPath $folder.path -Force -ErrorAction Stop} catch {$skipped++;continue}
        foreach ($entry in $entries) {
            if ($watch.Elapsed.TotalSeconds -gt $rd.timeout_s -or $visited -ge $rd.max_files -or $found.Count -ge $rd.limit) {$limited=$true;break}
            $visited++
            if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {$skipped++;continue}
            if (!$rd.include_hidden -and ($entry.Attributes -band [IO.FileAttributes]::Hidden)) {continue}
            $excluded=$false;foreach($glob in $rd.exclude) {if($entry.Name -like $glob){$excluded=$true;break}}
            if($excluded){continue}
            if ($entry.PSIsContainer) {
                if ($folder.depth -lt $rd.max_depth) {$queue.Enqueue(@{path=$entry.FullName;depth=$folder.depth+1})}
                continue
            }
            if ($entry.Name -notlike $rd.pattern) {continue}
            if ($rd.kind -eq 'find_files') {$found.Add((Info $entry.FullName));continue}
            if ($entry.Length -gt $rd.max_file_bytes) {$skipped++;continue}
            try {$decoded=Decode ([IO.File]::ReadAllBytes($entry.FullName)) $rd.encoding} catch {$skipped++;continue}
            $lines=$decoded.text -split "\r\n|\n|\r";$lineNo=0
            foreach ($line in $lines) {
                $lineNo++
                $match=$(if($regex){$regex.IsMatch($line)}else{$line.IndexOf($rd.query,$(if($rd.case_sensitive){[StringComparison]::Ordinal}else{[StringComparison]::OrdinalIgnoreCase})) -ge 0})
                if($match) {
                    $found.Add(@{path=$entry.FullName;line=$lineNo;text=$line.Substring(0,[Math]::Min($line.Length,2048));line_truncated=$line.Length -gt 2048})
                    if($found.Count -ge $rd.limit){$limited=$true;break}
                }
            }
        }
        if($limited){break}
    }
    return @{items=$found.ToArray();visited=$visited;skipped=$skipped;truncated=$limited;elapsed_ms=$watch.ElapsedMilliseconds}
}
try {
    $path=$(if($rd.remote_path){Guard $rd.remote_path}else{$null})
    $data=switch($rd.kind) {
        'cleanup_portable' {
            $manifest=Guard $rd.manifest_path
            if((HashFile $manifest) -ne $rd.manifest_sha256){Fail 'deployment_manifest_conflict'}
            $installed=[IO.File]::ReadAllText($manifest)|ConvertFrom-Json
            $root=Guard $installed.root
            if(!$manifest.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase) -or $installed.files.Count -gt 10000){Fail 'invalid_deployment_manifest'}
            $deleted=0;$pending=New-Object 'Collections.Generic.List[object]';$folders=New-Object 'Collections.Generic.HashSet[string]'
            foreach($entry in $installed.files){
                $file=Guard $entry.path
                if(!$file.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){Fail 'invalid_deployment_manifest'}
                $parent=[IO.Path]::GetDirectoryName($file)
                while($parent -and $parent.StartsWith($root,[StringComparison]::OrdinalIgnoreCase)){$folders.Add($parent)|Out-Null;if($parent -eq $root){break};$parent=[IO.Path]::GetDirectoryName($parent)}
                try{if([IO.File]::Exists($file)){DeleteVerified $file $entry.sha256};$deleted++}catch{$pending.Add(@{path=$file;reason='changed_or_busy'})}
            }
            if(!$pending.Count){
                DeleteVerified $manifest $rd.manifest_sha256;$deleted++
                foreach($folder in @($folders|Sort-Object Length -Descending)){try{[IO.Directory]::Delete((Guard $folder),$false)}catch{$pending.Add(@{path=$folder;reason='not_empty_or_busy'})}}
            }
            @{deleted_count=$deleted;cleanup_pending=$pending.ToArray();recursive=$false}
        }
        'prepare_stage' {
            $directory=$null
            foreach($base in @($env:ProgramData,$env:TEMP)){
                $candidate=Guard (Join-Path $base ('RustDeskMCP\sessions\'+$rd.session_id+'\tmp\'+$rd.operation_tag))
                if([IO.Directory]::Exists($candidate)){Fail 'staging_exists'}
                try{[IO.Directory]::CreateDirectory($candidate)|Out-Null;$directory=$candidate;break}catch [UnauthorizedAccessException] {continue}
            }
            if(!$directory){Fail 'workspace_unavailable'}
            @{directory=$directory;path=(Join-Path $directory 'payload.bin');owned=$true}
        }
        'commit_upload' {
            $stage=Guard $rd.stage_path
            if((HashFile $stage) -ne $rd.source_sha256){Fail 'transfer_hash_mismatch'}
            if($rd.mode -eq 'create'){
                $sameVolume=[IO.Path]::GetPathRoot($stage) -eq [IO.Path]::GetPathRoot($path)
                if($sameVolume){[IO.File]::Move($stage,$path)}else{
                    $input=[IO.File]::Open($stage,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
                    $output=[IO.File]::Open($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
                    try{$input.CopyTo($output);$output.Flush($true)}catch{Fail 'partial_upload_write'}finally{$input.Dispose();$output.Dispose()}
                }
                @{path=$path;sha256=$rd.source_sha256;size=(Get-Item -LiteralPath $path).Length;atomic=$sameVolume;backup_path=$null}
            }else{
                $target=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
                $source=$null;$backup=$null;$changed=$false
                try{
                    $sha=[Security.Cryptography.SHA256]::Create();try{$before=([BitConverter]::ToString($sha.ComputeHash($target))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
                    if(!$rd.expected_sha256 -or $before -ne $rd.expected_sha256){Fail 'file_conflict'}
                    $backup=Guard ($path+'.rdmcp.'+$rd.operation_tag+'.bak')
                    $copy=[IO.File]::Open($backup,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
                    try{$target.Position=0;$target.CopyTo($copy);$copy.Flush($true)}finally{$copy.Dispose()}
                    $source=[IO.File]::Open($stage,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
                    $changed=$true;$target.Position=0;$source.CopyTo($target);$target.SetLength($source.Length);$target.Flush($true)
                    @{path=$path;sha256=$rd.source_sha256;size=$source.Length;previous_sha256=$before;backup_path=$backup;atomic=$false;stage_path=$stage}
                }catch{
                    if($changed -and $backup){try{$restore=[IO.File]::OpenRead($backup);try{$target.Position=0;$restore.CopyTo($target);$target.SetLength($restore.Length);$target.Flush($true)}finally{$restore.Dispose()}}catch{Fail 'partial_write_restore_failed'}}
                    throw
                }finally{if($source){$source.Dispose()};$target.Dispose()}
            }
        }
        'exec_uploaded_script' {
            if((HashFile $path) -ne $rd.source_sha256){Fail 'script_hash_mismatch'}
            $bound=@{};foreach($property in $rd.parameters.PSObject.Properties){$bound[$property.Name]=$property.Value}
            $failed=$false;$global:LASTEXITCODE=0
            if($rd.shell_path){
                $shell=Guard $rd.shell_path
                if([IO.Path]::GetFileName($shell) -notin @('pwsh.exe','powershell.exe')){Fail 'unsupported_shell'}
                if($rd.shell_sha256 -and (HashFile $shell) -ne $rd.shell_sha256){Fail 'shell_hash_mismatch'}
                if($rd.manifest_path){
                    $manifest=Guard $rd.manifest_path
                    if((HashFile $manifest) -ne $rd.manifest_sha256){Fail 'deployment_manifest_conflict'}
                    $installed=[IO.File]::ReadAllText($manifest)|ConvertFrom-Json
                    $root=Guard $installed.root
                    if(!$shell.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){Fail 'shell_outside_deployment'}
                    foreach($entry in $installed.files){
                        $file=Guard $entry.path
                        if(!$file.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase) -or (HashFile $file) -ne $entry.sha256){Fail 'deployment_integrity_conflict'}
                    }
                }
                $raw=@{path=$path;parameters=$bound}|ConvertTo-Json -Depth 8 -Compress
                $encoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($raw))
                $invoke='$p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('''+$encoded+'''))|ConvertFrom-Json;$b=@{};foreach($v in $p.parameters.PSObject.Properties){$b[$v.Name]=$v.Value};$ErrorActionPreference=''Stop'';try{& $p.path @b;if($LASTEXITCODE){exit $LASTEXITCODE};if(!$?){exit 1}}catch{Write-Error $_;exit 1}'
                $argument=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($invoke))
                $output=(& $shell -NoLogo -NoProfile -NonInteractive -EncodedCommand $argument 2>&1|Out-String)
            }else{
                $output=(& $path @bound 2>&1|ForEach-Object {if($_ -is [Management.Automation.ErrorRecord]){$script:failed=$true};$_}|Out-String)
                if(!$?){$failed=$true}
            }
            @{path=$path;sha256=$rd.source_sha256;output=$output;success=(!$failed -and $global:LASTEXITCODE -eq 0);native_exit_code=$global:LASTEXITCODE;shell_path=$rd.shell_path}
        }
        'deploy_portable' {
            if((HashFile $path) -ne $rd.source_sha256){Fail 'download_hash_mismatch'}
            Add-Type -AssemblyName System.IO.Compression.FileSystem
            $destination=Guard ([IO.Path]::GetDirectoryName($path)+'\pwsh-'+$rd.version)
            if([IO.Directory]::Exists($destination)){Fail 'deployment_exists'}
            [IO.Directory]::CreateDirectory($destination)|Out-Null
            $archive=[IO.Compression.ZipFile]::OpenRead($path);$records=New-Object 'Collections.Generic.List[object]';$total=0L
            try {
                if($archive.Entries.Count -gt 10000){Fail 'archive_limit'}
                foreach($entry in $archive.Entries){
                    if($entry.FullName -match '(^[\\/]|:)' -or (($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000){Fail 'unsafe_archive_path'}
                    $target=Guard (Join-Path $destination $entry.FullName)
                    if(!$target.StartsWith($destination+'\',[StringComparison]::OrdinalIgnoreCase)){Fail 'unsafe_archive_path'}
                    $total+=$entry.Length;if($total -gt $rd.max_bytes){Fail 'archive_limit'}
                    if(!$entry.Name){[IO.Directory]::CreateDirectory($target)|Out-Null;continue}
                    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))|Out-Null
                    $input=$entry.Open();$output=[IO.File]::Open($target,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
                    try{$input.CopyTo($output);$output.Flush($true)}finally{$input.Dispose();$output.Dispose()}
                    $records.Add(@{path=$target;sha256=HashFile $target;kind='file'})
                }
            }finally{$archive.Dispose()}
            $exe=Guard (Join-Path $destination 'pwsh.exe')
            $probe=(& $exe -NoLogo -NoProfile -NonInteractive -Command '$PSVersionTable.PSVersion.ToString()'|Out-String).Trim()
            if($LASTEXITCODE -ne 0 -or $probe -ne $rd.version){Fail 'portable_probe_failed'}
            $manifest=Guard (Join-Path $destination '.rdmcp-manifest.json')
            $manifestBytes=[Text.Encoding]::UTF8.GetBytes((@{root=$destination;version=$probe;files=$records.ToArray()}|ConvertTo-Json -Depth 6 -Compress))
            $manifestStream=[IO.File]::Open($manifest,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
            try{$manifestStream.Write($manifestBytes,0,$manifestBytes.Length);$manifestStream.Flush($true)}finally{$manifestStream.Dispose()}
            $manifestHash=HashBytes $manifestBytes;$records.Add(@{path=$manifest;sha256=$manifestHash;kind='file'})
            @{path=$exe;version=$probe;architecture='x64';file_count=$records.Count;root=$destination;manifest_path=$manifest;manifest_sha256=$manifestHash;owned=$true;source_sha256=$rd.source_sha256;default_shell_changed=$false}
        }
        'get_file_info' {Info $path}
        'list_dir' {
            $items=@(Get-ChildItem -LiteralPath $path -Force | Sort-Object Name)
            $page=@($items | Select-Object -Skip $rd.offset -First $rd.limit | ForEach-Object {Info $_.FullName})
            @{path=$path;items=$page;offset=$rd.offset;next_offset=$rd.offset+$page.Count;has_more=$rd.offset+$page.Count -lt $items.Count;total=$items.Count}
        }
        'read_file' {
            $stream=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
            try {
                if($stream.Length -gt 8MB){Fail 'text_file_too_large'}
                $bytes=New-Object byte[] ([int]$stream.Length);$at=0
                while($at -lt $bytes.Length){$count=$stream.Read($bytes,$at,$bytes.Length-$at);if(!$count){Fail 'file_read_failed'};$at+=$count}
            } finally {$stream.Dispose()}
            $hash=HashBytes $bytes
            if($rd.expected_sha256 -and $hash -ne $rd.expected_sha256){Fail 'file_conflict'}
            $decoded=Decode $bytes $rd.encoding;$text=$decoded.text
            $newline=$(if($text.Contains("`r`n")){'crlf'}elseif($text.Contains("`n")){'lf'}else{'none'})
            if($rd.tail_lines -gt 0){$text=(@($text -split "\r\n|\n|\r" | Select-Object -Last $rd.tail_lines)-join "`n")}
            elseif($rd.start_line -gt 1){$text=(@($text -split "\r\n|\n|\r" | Select-Object -Skip ($rd.start_line-1))-join "`n")}
            $total=Points $text;if($rd.offset -gt $total){Fail 'invalid_offset'}
            $start=IndexAt $text $rd.offset;$end=IndexAt $text ($rd.offset+[Math]::Min($rd.max_chars,4096));$slice=$text.Substring($start,$end-$start)
            @{path=$path;text=$slice;sha256=$hash;encoding=$decoded.name;bom=$decoded.bom;newline=$newline;size=$bytes.Length;
              offset=$rd.offset;next_offset=$rd.offset+(Points $slice);has_more=$end -lt $text.Length;total_chars=$total}
        }
        {$_ -in 'write_file','edit_file'} {
            $create=$rd.kind -eq 'write_file' -and $rd.mode -eq 'create'
            $mode=$(if($create){[IO.FileMode]::CreateNew}else{[IO.FileMode]::Open})
            $stream=[IO.File]::Open($path,$mode,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
            $changed=$false;$original=$null
            try {
                if($stream.Length -gt 8MB){Fail 'text_file_too_large'}
                $original=New-Object byte[] ([int]$stream.Length);$at=0
                while($at -lt $original.Length){$count=$stream.Read($original,$at,$original.Length-$at);if(!$count){Fail 'file_read_failed'};$at+=$count}
                $before=HashBytes $original
                if(!$create -and (!$rd.expected_sha256 -or $before -ne $rd.expected_sha256)){Fail 'file_conflict'}
                $decoded=Decode $original $rd.encoding;$text=$decoded.text
                if($rd.kind -eq 'edit_file') {
                    if(!$rd.old_text){Fail 'empty_search_refused'}
                    $count=0;$at=0
                    while(($at=$text.IndexOf($rd.old_text,$at,[StringComparison]::Ordinal)) -ge 0){$count++;$at+=$rd.old_text.Length}
                    if(!$count){Fail 'text_not_found'};if(!$rd.replace_all -and $count -ne 1){Fail 'text_not_unique'}
                    $text=$text.Replace($rd.old_text,$rd.new_text)
                } elseif($rd.mode -eq 'append') {$text+=$rd.content} else {$text=$rd.content}
                if($rd.newline -eq 'lf'){$text=$text.Replace("`r`n","`n").Replace("`r","`n")}
                if($rd.newline -eq 'crlf'){$text=$text.Replace("`r`n","`n").Replace("`r","`n").Replace("`n","`r`n")}
                $encoded=$decoded.encoding.GetBytes($text)
                if($decoded.bom){$encoded=[byte[]]($original[0..($decoded.bom-1)]+$encoded)}
                $backup=$null
                if(!$create){$backup=Guard ($path+'.rdmcp.'+$rd.operation_tag+'.bak');$backupStream=[IO.File]::Open($backup,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);try{$backupStream.Write($original,0,$original.Length);$backupStream.Flush($true)}finally{$backupStream.Dispose()}}
                $changed=$true;$stream.Position=0;$stream.Write($encoded,0,$encoded.Length);$stream.SetLength($encoded.Length);$stream.Flush($true)
                @{path=$path;sha256=HashBytes $encoded;previous_sha256=$before;size=$encoded.Length;encoding=$decoded.name;bom=$decoded.bom;backup_path=$backup;atomic=$false}
            } catch {
                if($changed -and $null -ne $original){try{$stream.Position=0;$stream.Write($original,0,$original.Length);$stream.SetLength($original.Length);$stream.Flush($true)}catch{Fail 'partial_write_restore_failed'}}
                throw
            } finally {$stream.Dispose()}
        }
        'find_files' {Walk $path}
        'search_text' {Walk $path}
        'make_dir' {if([IO.Directory]::Exists($path)-or[IO.File]::Exists($path)){Fail 'path_exists'};[IO.Directory]::CreateDirectory($path)|Out-Null;@{path=$path;created=$true}}
        'rename_file' {
            $destination=Guard $rd.destination
            if([IO.File]::Exists($destination)-or[IO.Directory]::Exists($destination)){Fail 'destination_exists'}
            if([IO.Directory]::Exists($path)){[IO.Directory]::Move($path,$destination)}else{[IO.File]::Move($path,$destination)}
            @{source=$path;destination=$destination;renamed=$true}
        }
        'remove_file' {
            if([IO.Directory]::Exists($path)){[IO.Directory]::Delete($path,$false)}else{
                if(!$rd.expected_sha256 -or (HashFile $path)-ne $rd.expected_sha256){Fail 'file_conflict'}
                DeleteVerified $path $rd.expected_sha256
            }
            @{path=$path;removed=$true;recursive=$false}
        }
        'get_file_hash' {@{path=$path;algorithm='SHA256';sha256=HashFile $path}}
        'get_file_signature' {$s=Get-AuthenticodeSignature -LiteralPath $path;@{path=$path;status=$s.Status.ToString();message=$s.StatusMessage;signer=$(if($s.SignerCertificate){$s.SignerCertificate.Subject}else{$null})}}
        default {Fail 'unsupported_file_operation'}
    }
    @{ok=$true;data=$data}|ConvertTo-Json -Depth 10 -Compress
} catch {
    $message=$_.Exception.Message
    $code=$(if($message -match 'RDMCP:([a-z_]+)'){$Matches[1]}elseif($_.Exception -is [UnauthorizedAccessException]){'access_denied'}elseif($_.Exception -is [IO.FileNotFoundException]){'file_not_found'}elseif($_.Exception -is [IO.IOException]){'file_busy_or_io_error'}else{'file_operation_failed'})
    @{ok=$false;error=@{code=$code;stage=$rd.kind}}|ConvertTo-Json -Compress
}
