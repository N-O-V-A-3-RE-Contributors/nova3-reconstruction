# NOVA3 Control Center contributor-safe installation for Windows PowerShell.
# Legacy "NOVA3 Observatory" paths and the nova3-observer alias are preserved.
$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (Get-Command python -ErrorAction Stop).Source
& $python -c "import sys; assert sys.version_info >= (3, 10)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ required' }
& $python -c "import curses"
if ($LASTEXITCODE -ne 0) {
  Write-Host "Windows needs the optional Python 'windows-curses' package."
  $reply = Read-Host "Type INSTALL to install it for your user"
  if ($reply -ne 'INSTALL') { throw 'Install windows-curses before starting NOVA3 Control Center' }
  & $python -m pip install --user windows-curses
  if ($LASTEXITCODE -ne 0) { throw 'windows-curses installation failed' }
}
& $python -m unittest discover -s (Join-Path $source 'tests') -q
if ($LASTEXITCODE -ne 0) { throw 'NOVA3 Control Center tests failed; refusing installation' }
$old = Join-Path $env:LOCALAPPDATA 'NOVA3-Observatory'
$dest = Join-Path $env:LOCALAPPDATA 'NOVA3-Control-Center'
$stage = Join-Path $env:LOCALAPPDATA ('.NOVA3-Control-Center.stage.' + $PID)
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null
foreach ($file in @('observer.py','operator_ops.py','presence_view.py','progress.py','forecast_model.py',
                    'project_roadmap.json','ui_components.py','live_status.py','journal_inspector.py',
                    'onboarding.py','provider_accounts.py')) {
  Copy-Item -LiteralPath (Join-Path $source $file) -Destination (Join-Path $stage $file) -Force
}
# Provider metadata is owned by nova3_team/provider_runtime.py; without it the
# Accounts page silently disables, so require it and copy the optional Hermes
# sibling when present.
$runtime = Join-Path $source '../../nova3_team/provider_runtime.py'
if (!(Test-Path -LiteralPath $runtime)) { throw 'Missing nova3_team/provider_runtime.py; refusing installation' }
Copy-Item -LiteralPath $runtime -Destination (Join-Path $stage 'provider_runtime.py') -Force
$hermes = Join-Path $source '../../nova3_team/hermes_runtime.py'
if (Test-Path -LiteralPath $hermes) {
  Copy-Item -LiteralPath $hermes -Destination (Join-Path $stage 'hermes_runtime.py') -Force
}
# Offline synthetic health check before replacing the installed directory.
& $python -I (Join-Path $stage 'observer.py') --demo --once | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Staged NOVA3 Control Center health check failed; previous install preserved' }
# Fail closed if the provider registry/Accounts page could not load.
& $python -I -c "import sys; sys.path.insert(0, r'$stage'); import provider_accounts; assert provider_accounts.provider_runtime.PROVIDERS" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Staged Accounts/provider runtime check failed; previous install preserved' }
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Path (Join-Path $stage '*') -Destination $dest -Force
Remove-Item -Recurse -Force $stage
$launcher = Join-Path $dest 'nova3-control.cmd'
Set-Content -Encoding Ascii -Path $launcher -Value ('@echo off' + [Environment]::NewLine +
  'python "' + (Join-Path $dest 'observer.py') + '" %*')
# Compatibility launcher for existing shortcuts.
Set-Content -Encoding Ascii -Path (Join-Path $dest 'nova3-observer.cmd') -Value ('@echo off' + [Environment]::NewLine +
  'python "' + (Join-Path $dest 'observer.py') + '" %*')
Write-Host "NOVA3 Control Center installed: $launcher (alias: nova3-observer.cmd)"
if (Test-Path $old) { Write-Host "Previous Observatory files remain at: $old (state is not deleted)." }
Write-Host "Organization-owner or repository ADMIN access is rechecked for administrative tools; Windows systemd controls are unavailable."
Write-Host "A 60-second structured-event gap is shown as RUNNING with a structured-event-quiet sub-badge."
Write-Host "The existing NOVA3 worker is not modified."
