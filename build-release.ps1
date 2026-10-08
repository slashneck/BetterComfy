# Builds everything for a GitHub release into .\dist:
#   BetterComfySetup-<version>.exe              the installer, with Better Comfy inside
#   BetterComfy-<version>-win-x64.zip           the program alone, which the auto updater downloads
#   BetterComfy-<version>-win-x64.zip.sha256    its checksum, which the updater verifies
#   BetterComfy-<version>-source.zip            the source code without build output
#
# Needs Python 3.12 and the .NET 8 SDK. The version comes from bettercomfy\config.py.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

# Zip entries always use forward slashes, whatever the .NET version behind PowerShell does by default.
function New-Zip([string]$Folder, [string]$Destination, [string]$Prefix = '') {
    $archive = [IO.Compression.ZipFile]::Open($Destination, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in Get-ChildItem $Folder -Recurse -File) {
            $name = $Prefix + $file.FullName.Substring($Folder.Length + 1).Replace('\', '/')
            [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $file.FullName, $name, [IO.Compression.CompressionLevel]::Optimal)
        }
    } finally {
        $archive.Dispose()
    }
}

$version = (Select-String -Path 'bettercomfy\config.py' -Pattern '^VERSION = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Building Better Comfy $version"

# 1. Python environment
$python = '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    py -3.12 -m venv .venv
    & $python -m pip install --upgrade pip
}
& $python -m pip install -r requirements.txt --disable-pip-version-check -q
if ($LASTEXITCODE) { throw 'Installing the Python packages failed' }
& $python make_icon.py
& $python tools\make_banner.py

# 2. The program
$dist = Join-Path $PSScriptRoot 'dist'
$work = Join-Path $PSScriptRoot 'build'
if (Test-Path $dist) { Remove-Item $dist -Recurse -Force }
New-Item -ItemType Directory -Force $work | Out-Null
$v = $version.Split('.')
$versionFile = Join-Path $work 'version.txt'
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($($v[0]), $($v[1]), $($v[2]), 0), prodvers=($($v[0]), $($v[1]), $($v[2]), 0), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Better Comfy'),
      StringStruct('FileDescription', 'Better Comfy'),
      StringStruct('FileVersion', '$version'),
      StringStruct('InternalName', 'BetterComfy'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 slashneck'),
      StringStruct('OriginalFilename', 'BetterComfy.exe'),
      StringStruct('ProductName', 'Better Comfy'),
      StringStruct('ProductVersion', '$version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Encoding UTF8 $versionFile

$pyi = @(
    '--noconfirm', '--clean', '--onedir', '--windowed', '--log-level', 'WARN',
    '--name', 'BetterComfy',
    '--icon', (Join-Path $PSScriptRoot 'assets\icon.ico'),
    '--version-file', $versionFile,
    '--add-data', ((Join-Path $PSScriptRoot 'assets') + ';assets'),
    '--collect-data', 'imageio_ffmpeg', '--collect-binaries', 'imageio_ffmpeg',
    '--distpath', (Join-Path $work 'pyi-dist'), '--workpath', (Join-Path $work 'pyi-work'), '--specpath', $work
)
foreach ($m in 'tkinter', 'matplotlib', 'imageio', 'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml',
               'PySide6.QtQuick', 'PySide6.Qt3DCore', 'PySide6.QtMultimedia', 'PySide6.QtPdf', 'PySide6.QtCharts',
               'PySide6.QtDataVisualization', 'PySide6.QtBluetooth', 'PySide6.QtPositioning', 'PySide6.QtSql', 'PySide6.QtTest') {
    $pyi += '--exclude-module', $m
}
& $python -m PyInstaller @pyi (Join-Path $PSScriptRoot 'main.py')
if ($LASTEXITCODE) { throw 'Building Better Comfy failed' }
$app = Join-Path $work 'pyi-dist\BetterComfy'
# the spec file holds paths of the PC it was built on: it never leaves the build folder
Copy-Item 'LICENSE', 'THIRD-PARTY-NOTICES.md' $app

# 3. The file list. Updates and uninstalling only ever touch the files named here.
$files = Get-ChildItem $app -Recurse -File | ForEach-Object { $_.FullName.Substring($app.Length + 1) } | Sort-Object
[IO.File]::WriteAllLines((Join-Path $app 'bettercomfy-files.txt'), [string[]]$files, (New-Object Text.UTF8Encoding $false))

# 4. Zip and checksum
New-Item -ItemType Directory $dist | Out-Null
$zipName = "BetterComfy-$version-win-x64.zip"
$zip = Join-Path $dist $zipName
New-Zip $app $zip
$sha = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText("$zip.sha256", "$sha  $zipName`n")

# 5. Setup
$setupOut = Join-Path $work 'setup'
dotnet build 'setup\BetterComfy.Setup.csproj' -c Release "-p:Version=$version" "-p:PayloadZip=$zip" -o $setupOut --nologo
if ($LASTEXITCODE) { throw 'Building the setup failed' }
Move-Item (Join-Path $setupOut 'BetterComfySetup.exe') (Join-Path $dist "BetterComfySetup-$version.exe")

# 6. Source code, without build output
$source = Join-Path $work "BetterComfy-$version-source"
if (Test-Path $source) { Remove-Item $source -Recurse -Force }
New-Item -ItemType Directory $source | Out-Null
Copy-Item '.gitignore', 'LICENSE', 'README.md', 'THIRD-PARTY-NOTICES.md', 'build-release.ps1', 'requirements.txt', 'main.py', 'make_icon.py' $source
foreach ($dir in 'bettercomfy', 'assets', 'setup', 'tools', 'docs') {
    robocopy $dir (Join-Path $source $dir) /E /XD bin obj __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying $dir failed" }
}
$global:LASTEXITCODE = 0
New-Zip $source (Join-Path $dist "BetterComfy-$version-source.zip") "BetterComfy-$version-source/"
Remove-Item $work -Recurse -Force

Write-Host ''
Write-Host "Done. Release files are in $dist"
Get-ChildItem $dist | Format-Table Name, @{ Name = 'MB'; Expression = { [math]::Round($_.Length / 1MB, 1) } } -AutoSize
