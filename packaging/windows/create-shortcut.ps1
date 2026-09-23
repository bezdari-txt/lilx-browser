# Create a Start menu shortcut for lilx run from a source checkout, with the lilx icon.
#   powershell -ExecutionPolicy Bypass -File packaging\windows\create-shortcut.ps1
# (A PyInstaller build already has the icon in lilx.exe; shortcuts to it use that.)
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $Root "venv\Scripts\pythonw.exe"   # no console window
$Icon = Join-Path $Root "packaging\icons\lilx.ico"     # generated from logo.png
$Target = Join-Path ([Environment]::GetFolderPath("Programs")) "lilx.lnk"

$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut($Target)
$Link.TargetPath = $Python
$Link.Arguments = "-m lilx"
$Link.WorkingDirectory = $Root
$Link.IconLocation = "$Icon,0"
$Link.Description = "lilx web browser"
$Link.Save()
Write-Host "Created $Target"
