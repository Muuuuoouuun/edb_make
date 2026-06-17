param(
    [string]$AppName = "ClassInEDBMVP",
    [string]$OutputDir = "dist",
    [switch]$Clean,
    [switch]$Zip,
    [switch]$OneFile,
    [switch]$Console,
    [switch]$InstallPyInstaller,
    [switch]$SkipFrontendBuild,
    [string]$IconPath = "assets\app_icon.ico",
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
$SpecDir = Join-Path $ResolvedOutputDir "_pyinstaller_spec"
$FrontendBundle = Join-Path $ProjectRoot "ui_prototype\app.bundle.js"

if (-not $SkipFrontendBuild) {
    $NodeCommand = Get-Command node -ErrorAction SilentlyContinue
    if ($NodeCommand) {
        & $NodeCommand.Source (Join-Path $ProjectRoot "scripts\build_frontend_bundle.mjs")
    } elseif (-not (Test-Path $FrontendBundle)) {
        throw "Node.js is required to build ui_prototype\app.bundle.js. Install Node or run with -SkipFrontendBuild after creating the bundle."
    } else {
        Write-Warning "Node.js was not found; using existing ui_prototype\app.bundle.js."
    }
}

$HasPyInstaller = $true
& $PythonExe -m PyInstaller --version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    $HasPyInstaller = $false
}

if ($HasPyInstaller) {
    $ModeArg = if ($OneFile) { "--onefile" } else { "--onedir" }
    $WindowArg = if ($Console) { "--console" } else { "--windowed" }
    $ResolvedIconPath = if ([System.IO.Path]::IsPathRooted($IconPath)) { $IconPath } else { Join-Path $ProjectRoot $IconPath }
    $IconArgs = @()
    if (Test-Path $ResolvedIconPath) {
        $IconArgs = @("--icon", $ResolvedIconPath)
    } else {
        Write-Warning "Icon file not found: $ResolvedIconPath"
    }

    $DataItems = @(
        @("ui_prototype\index.html", "ui_prototype"),
        @("ui_prototype\board.html", "ui_prototype"),
        @("ui_prototype\reorder.js", "ui_prototype"),
        @("ui_prototype\review_filters.js", "ui_prototype"),
        @("ui_prototype\publish_summary.js", "ui_prototype"),
        @("ui_prototype\publish_guard.js", "ui_prototype"),
        @("ui_prototype\app.bundle.js", "ui_prototype"),
        @("ui_prototype\vendor\react.production.min.js", "ui_prototype\vendor"),
        @("ui_prototype\vendor\react-dom.production.min.js", "ui_prototype\vendor"),
        @("assets\app_icon.ico", "assets"),
        @("assets\app_icon.icns", "assets"),
        @("assets\app_icon.png", "assets")
    )
    $DataArgs = @()
    foreach ($Item in $DataItems) {
        $SourcePath = Join-Path $ProjectRoot $Item[0]
        if (Test-Path $SourcePath) {
            $DataArgs += @("--add-data", ($SourcePath + ";" + $Item[1]))
        }
    }

    $PyInstallerArgs = @(
        "--noconfirm",
        "--clean",
        $ModeArg,
        $WindowArg,
        "--distpath", $ResolvedOutputDir,
        "--specpath", $SpecDir,
        "--name", $AppName
    ) + $DataArgs + $IconArgs + @("app_server.py")

    & $PythonExe -m PyInstaller @PyInstallerArgs

    $PackageRoot = if ($OneFile) { Join-Path $ResolvedOutputDir "$AppName.exe" } else { Join-Path $ResolvedOutputDir $AppName }
    Write-Host "PyInstaller packaging complete."
} else {
    $PackageRoot = Join-Path $ResolvedOutputDir "source-package"
    New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null

    $ItemsToCopy = @(
        "app_server.py",
        "build_mvp_export.py",
        "build_structured_page_json.py",
        "preprocess.py",
        "segment.py",
        "ocr_backend.py",
        "placement_engine.py",
        "layout_template_schema.py",
        "structured_schema.py",
        "assemble_page.py",
        "edb_builder.py",
        "inspect_edb.py",
        "requirements-local.txt",
        "run_local_app.ps1",
        "scripts",
        "assets",
        "PACKAGING_MVP.md",
        "ui_prototype\index.html",
        "ui_prototype\board.html",
        "ui_prototype\reorder.js",
        "ui_prototype\review_filters.js",
        "ui_prototype\publish_summary.js",
        "ui_prototype\publish_guard.js",
        "ui_prototype\app.bundle.js",
        "ui_prototype\vendor\react.production.min.js",
        "ui_prototype\vendor\react-dom.production.min.js"
    )

    foreach ($Item in $ItemsToCopy) {
        $SourcePath = Join-Path $ProjectRoot $Item
        if (Test-Path $SourcePath) {
            $DestinationPath = Join-Path $PackageRoot $Item
            $DestinationParent = Split-Path -Parent $DestinationPath
            if ($DestinationParent) {
                New-Item -ItemType Directory -Force -Path $DestinationParent | Out-Null
            }
            Copy-Item -Recurse -Force $SourcePath $DestinationPath
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
