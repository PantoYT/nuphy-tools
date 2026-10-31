# Capture the USB traffic between NuPhyIO and the keyboard, to learn a part of its protocol.
# Run in an ADMIN PowerShell (USBPcap needs it). Captures every USB root hub at once, so it
# doesn't matter which port the keyboard is on. Files go to C:\Windows\Temp (not AppData).
#
#   .\capture.ps1 -Name travel
#   then do the thing in drive.nuphy.io, come back and press Enter
#   python pcap.py C:\Windows\Temp\nuphy_travel_hub2.pcap

param(
    [string]$Name = "capture",
    [string]$UsbPcap = "$env:ProgramFiles\USBPcap\USBPcapCMD.exe"
)

if (-not (Test-Path $UsbPcap)) { Write-Host "USBPcapCMD.exe not found at $UsbPcap (pass -UsbPcap)." -ForegroundColor Red; exit 1 }
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Run this in an ADMIN PowerShell." -ForegroundColor Red; exit 1
}
$procs = @()
foreach ($n in 1..10) {
    $dev = "\\.\USBPcap$n"
    $out = "$env:windir\Temp\nuphy_${Name}_hub$n.pcap"
    Remove-Item $out -ErrorAction SilentlyContinue
    $p = Start-Process $UsbPcap -ArgumentList "-d", $dev, "-o", $out, "-A" -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds 300
    if ($p.HasExited) { continue }
    $procs += $p
    Write-Host "capturing $dev -> $out"
}
if (-not $procs) { Write-Host "No USBPcap hubs - restart Windows after installing USBPcap." -ForegroundColor Red; exit 1 }
Write-Host "`nNow do it in drive.nuphy.io (connect the keyboard first)." -ForegroundColor Yellow
Read-Host "Press Enter when done"
$procs | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
Get-ChildItem "$env:windir\Temp\nuphy_${Name}_hub*.pcap" | Where-Object Length -gt 0 | Select-Object Name, Length
Write-Host "Done." -ForegroundColor Green
