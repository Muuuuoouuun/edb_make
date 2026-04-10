param(
    [string]$AppName = "ClassInEDBMVP",
    [string]$OutputDir = "dist",
    [switch]$Clean,
    [switch]$Zip,
    [switch]$InstallPyInstaller,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not $PythonExe) {
    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        $PythonExe = $VenvPython
    } else {
        $PythonExe = "python"
    }
}

if ($InstallPyInstaller) {
    & $PythonExe -m pip install pyinstaller
}

$ResolvedOutputDir = if ([System.IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $ProjectRoot $OutputDir }

if ($Clean -and (Test-Path $ResolvedOutputDir)) {
    Remove-Item -Recurse -Force $ResolvedOutputDir
}
New-Item -ItemType Directory -Force -Path $ResolvedOutputDir | Out-Null

$HasPyInstaller = $true
& $PythonExe -m PyInstaller --version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    $HasPyInstaller = $false
}

if ($HasPyInstaller) {
    $AddData = "ui_prototype;ui_prototype"
    & $PythonExe -m PyInstaller `
        --noconfirm `
        --clean `
        --onedir `
        --distpath $ResolvedOutputDir `
        --name $AppName `
        --add-data $AddData `
        app_server.py

    $PackageRoot = Join-Path $ResolvedOutputDir $AppName
    Write-Host "PyInstaller packaging complete."
} else {
    $PackageRoot = Join-Path $ResolvedOutputDir "source-package"
    New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null

    $ItemsToCopy = @(
        "app_server.py",
        "build_mvp_export.py",
        "build_problem_board_edb.py",
        "build_structured_page_json.py",
        "assemble_page.py",
        "edb_builder.py",
        "export_mvp_edb.py",
        "inspect_edb.py",
        "layout_template_schema.py",
        "ocr_backend.py",
        "page_repair.py",
        "pipeline_cache.py",
        "pipeline_router.py",
        "placement_engine.py",
        "preprocess.py",
        "segment.py",
        "structured_schema.py",
        "requirements-local.txt",
        "run_local_app.ps1",
        "PACKAGING_MVP.md",
        "ui_prototype"
    )

    foreach ($Item in $ItemsToCopy) {
        $SourcePath = Join-Path $ProjectRoot $Item
        if (Test-Path $SourcePath) {
            Copy-Item -Recurse -Force $SourcePath $PackageRoot
        }
    }

    Write-Warning "PyInstaller is not installed. Created source-package fallback instead."
}

if ($Zip) {
    $ZipPath = Join-Path $ResolvedOutputDir "$AppName.zip"
    if (Test-Path $ZipPath) {
        Remove-Item $ZipPath -Force
    }
    if (Test-Path $PackageRoot) {
        Compress-Archive -Path $PackageRoot -DestinationPath $ZipPath
        Write-Host "Zip archive: $ZipPath"
    }
}

Write-Host "Packaging complete."
Write-Host "Output folder: $PackageRoot"
