# Disk throughput probe: sequential write / read + many-small-files.
# Writes temp files with a HARD TIME LIMIT (so a degraded drive can never hang us), then deletes them.
# Pure ASCII on purpose (PS 5.1 parses BOM-less files as GBK).
#
# Usage (repo root):
#   powershell -File scripts/disk_speedtest.ps1                         # control=E:\ target=F:\
#   powershell -File scripts/disk_speedtest.ps1 -Control E:\ -Target F:\ -Seconds 20
#
# Why it exists: "copy is only tens of KB/s" is usually NOT bandwidth (both disks idle, queue 0)
# but per-file overhead (Explorer serial copy + AV filter + creating 50k tiny files on an HDD).
# This probe separates the two: sequential MB/s vs small-files/s.

param(
    [string]$Control = "E:\",
    [string]$Target  = "F:\",
    [int]$Seconds    = 20
)

function Measure-SeqWrite([string]$path, [int]$secs) {
    $buf = New-Object byte[] (1MB)
    (New-Object Random).NextBytes($buf)
    $fs = [IO.File]::Create($path)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $written = 0L
    try {
        while ($sw.Elapsed.TotalSeconds -lt $secs) {
            $fs.Write($buf, 0, $buf.Length)
            $written += $buf.Length
        }
        $fs.Flush()
    } catch {
        Write-Output ("    (write aborted: {0})" -f $_.Exception.Message)
    } finally {
        $fs.Close()
    }
    $sw.Stop()
    $mb = $written / 1MB
    "  seq-write  {0,10:N1} MB in {1,6:N1}s  =>  {2,8:N2} MB/s" -f $mb, $sw.Elapsed.TotalSeconds, `
        ($mb / [Math]::Max($sw.Elapsed.TotalSeconds, 0.001))
}

function Measure-SeqRead([string]$path, [int]$secs) {
    if (-not (Test-Path $path)) { "  seq-read   (no file)"; return }
    $buf = New-Object byte[] (1MB)
    $fs = [IO.File]::OpenRead($path)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $read = 0L
    try {
        while ($sw.Elapsed.TotalSeconds -lt $secs) {
            $n = $fs.Read($buf, 0, $buf.Length)
            if ($n -le 0) { break }
            $read += $n
        }
    } catch {
        Write-Output ("    (read aborted: {0})" -f $_.Exception.Message)
    } finally {
        $fs.Close()
    }
    $sw.Stop()
    $mb = $read / 1MB
    "  seq-read   {0,10:N1} MB in {1,6:N1}s  =>  {2,8:N2} MB/s" -f $mb, $sw.Elapsed.TotalSeconds, `
        ($mb / [Math]::Max($sw.Elapsed.TotalSeconds, 0.001))
}

function Measure-SmallFiles([string]$dir, [int]$count, [int]$secs) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $buf = New-Object byte[] (64KB)
    (New-Object Random).NextBytes($buf)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $done = 0
    try {
        while ($done -lt $count -and $sw.Elapsed.TotalSeconds -lt $secs) {
            [IO.File]::WriteAllBytes((Join-Path $dir ("f{0:D5}.bin" -f $done)), $buf)
            $done++
        }
    } catch {
        Write-Output ("    (small-file aborted: {0})" -f $_.Exception.Message)
    }
    $sw.Stop()
    "  small-files  {0,7:N0} files in {1,6:N1}s  =>  {2,8:N2} files/s, {3,7:N2} MB/s" -f `
        $done, $sw.Elapsed.TotalSeconds, ($done / [Math]::Max($sw.Elapsed.TotalSeconds, 0.001)), `
        (($done * 64KB / 1MB) / [Math]::Max($sw.Elapsed.TotalSeconds, 0.001))
}

Write-Host "=== 1) CONTROL (internal disk): $Control ==="
Measure-SeqWrite (Join-Path $Control "_speedprobe_tmp.bin") $Seconds

Write-Host "=== 2) TARGET (external/USB disk): $Target ==="
Measure-SeqWrite (Join-Path $Target "_speedprobe_tmp.bin") $Seconds
Measure-SeqRead  (Join-Path $Target "_speedprobe_tmp.bin") ([Math]::Max(5, $Seconds - 5))
Measure-SmallFiles (Join-Path $Target "_speedprobe_dir") 300 $Seconds

Write-Host "=== 3) cleanup ==="
foreach ($p in @((Join-Path $Control "_speedprobe_tmp.bin"), (Join-Path $Target "_speedprobe_tmp.bin"))) {
    if (Test-Path $p) {
        $sz = (Get-Item $p).Length / 1MB
        Remove-Item $p -Force
        "  removed $p ({0:N1} MB)" -f $sz
    }
}
$d = Join-Path $Target "_speedprobe_dir"
if (Test-Path $d) { Remove-Item $d -Recurse -Force; "  removed $d" }
Write-Host "done"
