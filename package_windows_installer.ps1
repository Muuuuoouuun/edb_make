param(
    [string]$AppName = "ClassInEDBMVP",
    [string]$OutputDir = "dist",
    [string]$Version = "",
    [string]$UpdateFeedUrl = "",
    [string]$DownloadUrl = "",
    [string]$ReleaseNotesUrl = "",
    [switch]$Clean,
    [switch]$SkipAppBuild,
    [switch]$InstallPyInstaller,
    [switch]$Sign,
    [string]$SignTool = "",
    [string]$SignCertificatePath = "",
    [string]$SignCertificatePassword = "",
    [string]$SignCertificateSubject = "",
    [string]$SignCertificateThumbprint = "",
    [switch]$SignCertificateAutoSelect,
    [string]$SignTimestampUrl = "http://timestamp.digicert.com",
    [string]$PythonExe = "",
    [string]$InnoSetupCompiler = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
. (Join-Path $ProjectRoot "scripts\Sign-WindowsArtifact.ps1")
$ResolvedOutputDir = if ([System.IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $ProjectRoot $OutputDir }

if (-not $PythonExe) {
    $VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        $PythonExe = $VenvPython
    } else {
        $PythonExe = "python"
    }
}

function Find-InnoSetupCompiler {
    param([string]$Requested)

    if ($Requested -and (Test-Path $Requested)) {
        return $Requested
    }

    $Candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )

    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path $Candidate)) {
            return $Candidate
        }
    }

    $Command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }

    return ""
}

function Get-JsonStringProperty {
    param(
        [Parameter(Mandatory = $true)] [object]$Object,
        [Parameter(Mandatory = $true)] [string]$Name
    )

    if (-not $Object.PSObject.Properties[$Name]) {
        return ""
    }
    return [string]$Object.PSObject.Properties[$Name].Value
}

function Read-PackagedUpdateConfig {
    param([Parameter(Mandatory = $true)] [string]$PackageRoot)

    $Candidates = @(
        (Join-Path $PackageRoot "app_update_config.json"),
        (Join-Path $PackageRoot "_internal\app_update_config.json")
    )
    foreach ($Candidate in $Candidates) {
        if (Test-Path $Candidate) {
            try {
                return Get-Content -Raw -Path $Candidate | ConvertFrom-Json
            } catch {
                throw "Could not read packaged update metadata: $Candidate. $($_.Exception.Message)"
            }
        }
    }
    throw "Packaged update metadata was not found under: $PackageRoot"
}

if (-not $SkipAppBuild) {
    $AppBuildArgs = @{
        AppName = $AppName
        OutputDir = $OutputDir
    }
    if ($Version) {
        $AppBuildArgs.Version = $Version
    }
    if ($UpdateFeedUrl) {
        $AppBuildArgs.UpdateFeedUrl = $UpdateFeedUrl
    }
    if ($DownloadUrl) {
        $AppBuildArgs.DownloadUrl = $DownloadUrl
    }
    if ($ReleaseNotesUrl) {
        $AppBuildArgs.ReleaseNotesUrl = $ReleaseNotesUrl
    }
    if ($Clean) {
        $AppBuildArgs.Clean = $true
    }
    if ($InstallPyInstaller) {
        $AppBuildArgs.InstallPyInstaller = $true
    }
    $AppBuildArgs.RequirePyInstaller = $true
    if ($PythonExe) {
        $AppBuildArgs.PythonExe = $PythonExe
    }
    if ($Sign) {
        $AppBuildArgs.Sign = $true
        $AppBuildArgs.SignTool = $SignTool
        $AppBuildArgs.SignCertificatePath = $SignCertificatePath
        $AppBuildArgs.SignCertificatePassword = $SignCertificatePassword
        $AppBuildArgs.SignCertificateSubject = $SignCertificateSubject
        $AppBuildArgs.SignCertificateThumbprint = $SignCertificateThumbprint
        $AppBuildArgs.SignCertificateAutoSelect = $SignCertificateAutoSelect
        $AppBuildArgs.SignTimestampUrl = $SignTimestampUrl
    }
    & (Join-Path $ProjectRoot "package_mvp.ps1") @AppBuildArgs
}

$PackageRoot = Join-Path $ResolvedOutputDir $AppName
$PackageExe = Join-Path $PackageRoot "$AppName.exe"
if (-not (Test-Path $PackageExe)) {
    throw "PyInstaller app output was not found: $PackageExe. Build the app first or remove -SkipAppBuild."
}

$PackagedUpdateConfig = Read-PackagedUpdateConfig $PackageRoot
$PackagedVersion = Get-JsonStringProperty $PackagedUpdateConfig "version"
$EffectiveInstallerVersion = if ($Version) { $Version } else { $PackagedVersion }
if (-not $EffectiveInstallerVersion) {
    throw "Packaged update metadata does not include a version, and -Version was not provided."
}

$VerifierArgs = @(
    (Join-Path $ProjectRoot "scripts\verify_packaged_app.py"),
    $PackageRoot,
    "--expected-app-name",
    $AppName,
    "--expected-version",
    $EffectiveInstallerVersion
)
& $PythonExe @VerifierArgs

if ($Sign -and $SkipAppBuild) {
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

$InstallerPath = Join-Path $ResolvedOutputDir "$AppName-Setup.exe"
if (Test-Path $InstallerPath) {
    Remove-Item -Force $InstallerPath
}

$Iscc = Find-InnoSetupCompiler $InnoSetupCompiler
if (-not $Iscc) {
    throw "Inno Setup 6 compiler(ISCC.exe)를 찾지 못했습니다. https://jrsoftware.org/isinfo.php 에서 설치한 뒤 다시 실행하거나 -InnoSetupCompiler 경로를 지정하세요."
}

$InstallerScript = Join-Path $ProjectRoot "installer\windows\ClassInEDBMVP.iss"
$IsccArgs = @(
    "/DAppName=$AppName",
    "/DSourceDir=$PackageRoot",
    "/DOutputDir=$ResolvedOutputDir",
    "/DAppVersion=$EffectiveInstallerVersion"
)
& $Iscc @IsccArgs $InstallerScript

if ($Sign) {
    Invoke-EDBWindowsSignature `
        -Path $InstallerPath `
        -SignTool $SignTool `
        -CertificatePath $SignCertificatePath `
        -CertificatePassword $SignCertificatePassword `
        -CertificateSubject $SignCertificateSubject `
        -CertificateThumbprint $SignCertificateThumbprint `
        -CertificateAutoSelect:$SignCertificateAutoSelect `
        -TimestampUrl $SignTimestampUrl `
        -Description "$AppName Setup"
}
Write-Host "Installer complete."
Write-Host "Installer: $InstallerPath"
