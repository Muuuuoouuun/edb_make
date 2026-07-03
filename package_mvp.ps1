param(
    [string]$AppName = "ClassInEDBMVP",
    [string]$AppId = "",
    [string]$OutputDir = "dist",
    [string]$Version = "",
    [string]$UpdateFeedUrl = "",
    [string]$DownloadUrl = "",
    [string]$ReleaseNotesUrl = "",
    [switch]$Clean,
    [switch]$Zip,
    [switch]$OneFile,
    [switch]$Console,
    [switch]$InstallPyInstaller,
    [switch]$SkipFrontendBuild,
    [switch]$RequirePyInstaller,
    [switch]$Sign,
    [string]$SignTool = "",
    [string]$SignCertificatePath = "",
    [string]$SignCertificatePassword = "",
    [string]$SignCertificateSubject = "",
    [string]$SignCertificateThumbprint = "",
    [switch]$SignCertificateAutoSelect,
    [string]$SignTimestampUrl = "http://timestamp.digicert.com",
    [string]$IconPath = "assets\app_icon.ico",
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
. (Join-Path $ProjectRoot "scripts\Sign-WindowsArtifact.ps1")

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

function Remove-EDBPathIfExists {
    param([Parameter(Mandatory = $true)] [string]$Path)

    if (Test-Path $Path) {
        Remove-Item -Recurse -Force $Path
    }
}

function Assert-EDBNonEmptyFile {
    param(
        [Parameter(Mandatory = $true)] [string]$Path,
        [Parameter(Mandatory = $true)] [string]$Label
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label was not created: $Path"
    }
    if ((Get-Item -LiteralPath $Path).Length -le 0) {
        throw "$Label is empty: $Path"
    }
}

function Assert-EDBZipContainsEntry {
    param(
        [Parameter(Mandatory = $true)] [string]$ZipPath,
        [Parameter(Mandatory = $true)] [string]$EntryName
    )

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Archive = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        $Expected = $EntryName.Replace("\", "/")
        $Found = $false
        foreach ($Entry in $Archive.Entries) {
            if ($Entry.FullName.Replace("\", "/") -eq $Expected) {
                $Found = $true
                break
            }
        }
        if (-not $Found) {
            throw "Zip archive is missing expected entry: $EntryName"
        }
    } finally {
        $Archive.Dispose()
    }
}

function Get-EDBJsonStringProperty {
    param(
        [Parameter(Mandatory = $true)] [object]$Object,
        [Parameter(Mandatory = $true)] [string[]]$Names
    )

    foreach ($Name in $Names) {
        if ($Object.PSObject.Properties[$Name]) {
            $Value = ([string]$Object.PSObject.Properties[$Name].Value).Trim()
            if ($Value) {
                return $Value
            }
        }
    }
    return ""
}

if ($Clean -and (Test-Path $ResolvedOutputDir)) {
    Remove-Item -Recurse -Force $ResolvedOutputDir
}
New-Item -ItemType Directory -Force -Path $ResolvedOutputDir | Out-Null
$PackageDirPath = Join-Path $ResolvedOutputDir $AppName
$PackageExePath = Join-Path $ResolvedOutputDir "$AppName.exe"
$SourcePackagePath = Join-Path $ResolvedOutputDir "source-package"
$ZipPath = Join-Path $ResolvedOutputDir "$AppName.zip"
$WorkPath = Join-Path $ResolvedOutputDir "_pyinstaller_build"
foreach ($StalePath in @($WorkPath, $PackageDirPath, $PackageExePath, $SourcePackagePath, $ZipPath)) {
    Remove-EDBPathIfExists $StalePath
}
$SpecDir = Join-Path $ResolvedOutputDir "_pyinstaller_spec"
New-Item -ItemType Directory -Force -Path $SpecDir | Out-Null
$FrontendBundle = Join-Path $ProjectRoot "ui_prototype\app.bundle.js"
$BuildUpdateConfig = Join-Path $SpecDir "app_update_config.json"
$ProjectUpdateConfig = Join-Path $ProjectRoot "app_update_config.json"
$UpdateConfigScript = Join-Path $ProjectRoot "scripts\build_app_update_config.py"
$UpdateConfigEnv = @{
    EDB_PACKAGE_APP_ID = $AppId
    EDB_PACKAGE_APP_NAME = $AppName
    EDB_PACKAGE_APP_VERSION = $Version
    EDB_PACKAGE_UPDATE_FEED_URL = $UpdateFeedUrl
    EDB_PACKAGE_DOWNLOAD_URL = $DownloadUrl
    EDB_PACKAGE_RELEASE_NOTES_URL = $ReleaseNotesUrl
}
$PreviousUpdateConfigEnv = @{}
foreach ($Name in $UpdateConfigEnv.Keys) {
    $PreviousUpdateConfigEnv[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
    [Environment]::SetEnvironmentVariable($Name, $UpdateConfigEnv[$Name], "Process")
}
try {
    & $PythonExe $UpdateConfigScript $ProjectUpdateConfig $BuildUpdateConfig
    if ($LASTEXITCODE -ne 0) {
        throw "app_update_config generation failed."
    }
} finally {
    foreach ($Name in $PreviousUpdateConfigEnv.Keys) {
        [Environment]::SetEnvironmentVariable($Name, $PreviousUpdateConfigEnv[$Name], "Process")
    }
}
$UpdateConfig = Get-Content -Raw -Path $BuildUpdateConfig | ConvertFrom-Json
$EffectiveAppId = Get-EDBJsonStringProperty -Object $UpdateConfig -Names @("appId")
$EffectiveAppName = Get-EDBJsonStringProperty -Object $UpdateConfig -Names @("appName")
$EffectiveAppVersion = Get-EDBJsonStringProperty -Object $UpdateConfig -Names @("version")
$EffectiveUpdateFeedUrl = Get-EDBJsonStringProperty -Object $UpdateConfig -Names @("updateFeedUrl")
$EffectiveDownloadUrl = Get-EDBJsonStringProperty -Object $UpdateConfig -Names @("downloadUrl")
$EffectiveReleaseNotesUrl = Get-EDBJsonStringProperty -Object $UpdateConfig -Names @("releaseNotesUrl")

function Invoke-EDBPackagedAppVerifier {
    param([Parameter(Mandatory = $true)] [string]$PackageRoot)

    $VerifierArgs = @(
        (Join-Path $ProjectRoot "scripts\verify_packaged_app.py"),
        $PackageRoot,
        "--expected-app-id",
        $EffectiveAppId,
        "--expected-app-name",
        $EffectiveAppName,
        "--expected-version",
        $EffectiveAppVersion
    )
    if ($EffectiveUpdateFeedUrl) {
        $VerifierArgs += @("--expected-update-feed-url", $EffectiveUpdateFeedUrl)
    }
    if ($EffectiveDownloadUrl) {
        $VerifierArgs += @("--expected-download-url", $EffectiveDownloadUrl)
    }
    if ($EffectiveReleaseNotesUrl) {
        $VerifierArgs += @("--expected-release-notes-url", $EffectiveReleaseNotesUrl)
    }
    & $PythonExe @VerifierArgs
}

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

& $PythonExe (Join-Path $ProjectRoot "scripts\verify_frontend_package.py") --root $ProjectRoot

$HasPyInstaller = $true
& $PythonExe -m PyInstaller --version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    $HasPyInstaller = $false
}

$BuiltWithPyInstaller = $false
if ($HasPyInstaller) {
    $BuiltWithPyInstaller = $true
    $ModeArg = if ($OneFile) { "--onefile" } else { "--onedir" }
    $WindowArg = if ($Console) { "--console" } else { "--windowed" }
    $PackageRoot = if ($OneFile) { $PackageExePath } else { $PackageDirPath }
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
        @("scripts\render_hwp_with_rhwp_core.mjs", "scripts"),
        @("assets\app_icon.png", "assets")
    )
    $DataArgs = @()
    $DataArgs += @("--add-data", ($BuildUpdateConfig + ";."))
    foreach ($Item in $DataItems) {
        $SourcePath = Join-Path $ProjectRoot $Item[0]
        if (Test-Path $SourcePath) {
            $DataArgs += @("--add-data", ($SourcePath + ";" + $Item[1]))
        }
    }
    $HiddenImportArgs = @(
        "--hidden-import", "preprocess",
        "--hidden-import", "build_mvp_export",
        "--hidden-import", "build_problem_board_edb",
        "--hidden-import", "build_structured_page_json",
        "--hidden-import", "edb_builder",
        "--hidden-import", "page_repair",
        "--hidden-import", "image_reconstruction_backend"
    )

    $PyInstallerArgs = @(
        "--noconfirm",
        "--clean",
        $ModeArg,
        $WindowArg,
        "--distpath", $ResolvedOutputDir,
        "--specpath", $SpecDir,
        "--workpath", $WorkPath,
        "--name", $AppName
    ) + $DataArgs + $HiddenImportArgs + $IconArgs + @("app_server.py")

    & $PythonExe -m PyInstaller @PyInstallerArgs

    if ($OneFile) {
        Assert-EDBNonEmptyFile -Path $PackageRoot -Label "PyInstaller one-file executable"
    } else {
        Invoke-EDBPackagedAppVerifier -PackageRoot $PackageRoot
    }
    Remove-EDBPathIfExists $WorkPath
    Write-Host "PyInstaller packaging complete."
} else {
    if ($RequirePyInstaller) {
        throw "PyInstaller is required for this packaging mode. Run with -InstallPyInstaller or install PyInstaller first."
    }
    $PackageRoot = $SourcePackagePath
    New-Item -ItemType Directory -Force -Path $PackageRoot | Out-Null

    $ItemsToCopy = @(
        "app_server.py",
        "build_mvp_export.py",
        "build_problem_board_edb.py",
        "build_structured_page_json.py",
        "image_reconstruction_backend.py",
        "page_repair.py",
        "pipeline_cache.py",
        "pipeline_router.py",
        "preprocess.py",
        "segment.py",
        "ocr_backend.py",
        "placement_engine.py",
        "layout_template_schema.py",
        "structured_schema.py",
        "user_settings.py",
        "assemble_page.py",
        "edb_builder.py",
        "inspect_edb.py",
        "requirements-local.txt",
        "run_local_app.ps1",
        "scripts\render_hwp_with_rhwp_core.mjs",
        "assets\app_icon.png",
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
    Copy-Item -Force $BuildUpdateConfig (Join-Path $PackageRoot "app_update_config.json")

    Invoke-EDBPackagedAppVerifier -PackageRoot $PackageRoot
    Write-Warning "PyInstaller is not installed. Created source-package fallback instead."
}

if ($Zip) {
    if ($Sign) {
        Invoke-EDBWindowsPackageSigning `
            -PackagePath $PackageRoot `
            -SignTool $SignTool `
            -CertificatePath $SignCertificatePath `
            -CertificatePassword $SignCertificatePassword `
            -CertificateSubject $SignCertificateSubject `
            -CertificateThumbprint $SignCertificateThumbprint `
            -CertificateAutoSelect:$SignCertificateAutoSelect `
            -TimestampUrl $SignTimestampUrl `
            -Description $AppName
    }

    if (Test-Path $PackageRoot) {
        Compress-Archive -Path $PackageRoot -DestinationPath $ZipPath
        Assert-EDBNonEmptyFile -Path $ZipPath -Label "Zip archive"
        if ($BuiltWithPyInstaller -and $OneFile) {
            Assert-EDBZipContainsEntry -ZipPath $ZipPath -EntryName "$AppName.exe"
        } elseif ($BuiltWithPyInstaller) {
            Assert-EDBZipContainsEntry -ZipPath $ZipPath -EntryName "$AppName/$AppName.exe"
        } elseif (-not $BuiltWithPyInstaller) {
            Assert-EDBZipContainsEntry -ZipPath $ZipPath -EntryName "source-package/app_update_config.json"
        }
        Write-Host "Zip archive: $ZipPath"
    }
} elseif ($Sign) {
    Invoke-EDBWindowsPackageSigning `
        -PackagePath $PackageRoot `
        -SignTool $SignTool `
        -CertificatePath $SignCertificatePath `
        -CertificatePassword $SignCertificatePassword `
        -CertificateSubject $SignCertificateSubject `
        -CertificateThumbprint $SignCertificateThumbprint `
        -CertificateAutoSelect:$SignCertificateAutoSelect `
        -TimestampUrl $SignTimestampUrl `
        -Description $AppName
}

Write-Host "Packaging complete."
Write-Host "Output folder: $PackageRoot"
