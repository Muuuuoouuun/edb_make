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
    [string]$PythonExe = "",
    [string]$InnoSetupCompiler = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot
$ResolvedOutputDir = if ([System.IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $ProjectRoot $OutputDir }

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
    & (Join-Path $ProjectRoot "package_mvp.ps1") @AppBuildArgs
}

$PackageRoot = Join-Path $ResolvedOutputDir $AppName
$PackageExe = Join-Path $PackageRoot "$AppName.exe"
if (-not (Test-Path $PackageExe)) {
    throw "PyInstaller app output was not found: $PackageExe. Build the app first or remove -SkipAppBuild."
}

$Iscc = Find-InnoSetupCompiler $InnoSetupCompiler
if (-not $Iscc) {
    throw "Inno Setup 6 compiler(ISCC.exe)를 찾지 못했습니다. https://jrsoftware.org/isinfo.php 에서 설치한 뒤 다시 실행하거나 -InnoSetupCompiler 경로를 지정하세요."
}

$InstallerScript = Join-Path $ProjectRoot "installer\windows\ClassInEDBMVP.iss"
$IsccArgs = @(
    "/DAppName=$AppName",
    "/DSourceDir=$PackageRoot",
    "/DOutputDir=$ResolvedOutputDir"
)
if ($Version) {
    $IsccArgs += "/DAppVersion=$Version"
}
& $Iscc @IsccArgs $InstallerScript

$InstallerPath = Join-Path $ResolvedOutputDir "$AppName-Setup.exe"
Write-Host "Installer complete."
Write-Host "Installer: $InstallerPath"
