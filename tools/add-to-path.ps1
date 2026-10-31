# Puts this folder on your user PATH, so "nuphy" works in any new terminal. Run it once:
#   powershell -ExecutionPolicy Bypass -File tools\add-to-path.ps1          (add)
#   powershell -ExecutionPolicy Bypass -File tools\add-to-path.ps1 -Remove  (take it off again)
param([switch]$Remove)
$dir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$cur = [Environment]::GetEnvironmentVariable('Path', 'User')
$parts = @($cur -split ';' | Where-Object { $_ -and $_.TrimEnd('\') -ne $dir.TrimEnd('\') })
if (-not $Remove) { $parts += $dir }
[Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
if ($Remove) { "removed $dir from PATH" } else { "added $dir to PATH - open a new terminal and type: nuphy" }
