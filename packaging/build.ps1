param(
    [string]$Version = "0.1.0",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

& $Python packaging\generate_icon.py
& $Python -c "from jarvis.audio.wakeword import model_path; model_path('hey_jarvis')"
& $Python -m PyInstaller --clean --noconfirm packaging\jarvis.spec

$ZipOut = Join-Path $Root "dist\Jarvis-$Version-portable.zip"
if (Test-Path $ZipOut) { Remove-Item $ZipOut -Force }
& $Python -c "import shutil; shutil.make_archive(r'$Root\dist\Jarvis-$Version-portable', 'zip', r'$Root\dist\Jarvis')"

if (-not $SkipInstaller) {
    $IsccCandidates = @(
        (Get-Command iscc -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }

    if ($IsccCandidates.Count -gt 0) {
        & $IsccCandidates[0] "/DMyAppVersion=$Version" "packaging\jarvis.iss"
    } else {
        Write-Host "Inno Setup 6 not found locally; skipped installer .exe."
    }
}

Get-ChildItem -Path (Join-Path $Root "dist") -Depth 1 | Select-Object Name, Length
