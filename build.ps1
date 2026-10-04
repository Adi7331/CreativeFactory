param(
    [Parameter(Mandatory=$true)][string]$FfmpegDir,
    [string]$OutputDir = "dist"
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$ffmpeg = Join-Path $FfmpegDir 'ffmpeg.exe'
$ffprobe = Join-Path $FfmpegDir 'ffprobe.exe'
$license = Join-Path $FfmpegDir 'LICENSE'
$readme = Join-Path $FfmpegDir 'README.txt'
if (!(Test-Path -LiteralPath $ffmpeg) -or !(Test-Path -LiteralPath $ffprobe)) {
    throw 'Podaj folder zawierający ffmpeg.exe i ffprobe.exe.'
}
if (!(Test-Path -LiteralPath $license) -or !(Test-Path -LiteralPath $readme)) {
    throw 'Folder FFmpeg musi zawierać także LICENSE i README.txt.'
}
if (!(Get-Command python -ErrorAction SilentlyContinue)) { throw 'Do budowy potrzebny jest Python. Użytkownicy ZIP go nie potrzebują.' }
Push-Location $repo
try {
    $venv = Join-Path $repo '.build-venv'
    $builder = Join-Path $venv 'Scripts\python.exe'
    if (!(Test-Path -LiteralPath $builder)) { python -m venv $venv }
    & $builder -m pip install -r requirements-build.txt
    & $builder -m PyInstaller --noconfirm --clean --onedir --windowed --name CreativeFactory `
        --distpath $OutputDir --workpath build --specpath build `
        --paths $repo `
        --add-data "$(Join-Path $repo 'config.default.json');." `
        --add-binary "${ffmpeg};tools/ffmpeg" `
        --add-binary "${ffprobe};tools/ffmpeg" main.py
    $program = Join-Path $OutputDir 'CreativeFactory'
    Copy-Item -LiteralPath $license -Destination (Join-Path $program 'FFmpeg-LICENSE.txt')
    Copy-Item -LiteralPath $readme -Destination (Join-Path $program 'FFmpeg-README.txt')
    Copy-Item -LiteralPath (Join-Path $repo 'LGPL-3.0.txt') -Destination $program
    Copy-Item -LiteralPath (Join-Path $repo 'GPL-3.0.txt') -Destination $program
    Copy-Item -LiteralPath (Join-Path $repo 'THIRD_PARTY.md') -Destination $program
    Copy-Item -LiteralPath (Join-Path $repo 'START_HERE.txt') -Destination $program
    $pythonLicense = & $builder -c "import sys; from pathlib import Path; print(Path(sys.base_prefix) / 'LICENSE.txt')"
    if (Test-Path -LiteralPath $pythonLicense) {
        Copy-Item -LiteralPath $pythonLicense -Destination (Join-Path $program 'Python-LICENSE.txt')
    }
    Write-Host "Gotowe: $(Join-Path $OutputDir 'CreativeFactory')"
}
finally { Pop-Location }

