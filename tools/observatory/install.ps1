# NOVA3 Observatory contributor-safe installation for Windows PowerShell.
$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (Get-Command python -ErrorAction Stop).Source
& $python -c "import sys; assert sys.version_info >= (3, 10)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ required' }
& $python -c "import curses"
if ($LASTEXITCODE -ne 0) {
  Write-Host "Windows needs the optional Python 'windows-curses' package."
  $reply = Read-Host "Type INSTALL to install it for your user"
  if ($reply -ne 'INSTALL') { throw 'Install windows-curses before starting Observatory' }
  & $python -m pip install --user windows-curses
  if ($LASTEXITCODE -ne 0) { throw 'windows-curses installation failed' }
}
& $python -m unittest discover -s (Join-Path $source 'tests') -q
if ($LASTEXITCODE -ne 0) { throw 'Observatory tests failed; refusing installation' }
$dest = Join-Path $env:LOCALAPPDATA 'NOVA3-Observatory'
New-Item -ItemType Directory -Force -Path $dest | Out-Null
foreach ($file in @('observer.py','operator_ops.py','presence_view.py','progress.py','project_roadmap.json')) {
  Copy-Item -LiteralPath (Join-Path $source $file) -Destination (Join-Path $dest $file) -Force
}
$launcher = Join-Path $dest 'nova3-observer.cmd'
Set-Content -Encoding Ascii -Path $launcher -Value ('@echo off' + [Environment]::NewLine +
  'python "' + (Join-Path $dest 'observer.py') + '" %*')
Write-Host "Installed: $launcher"
Write-Host "Github ADMIN or MAINTAIN access is required for owner tools; Windows systemd controls are unavailable."
Write-Host "The existing NOVA3 worker is not modified."
