param(
    [string]$AppName = "ClassInEDBMVP",
    [string]$OutputDir = "dist",
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
    if ($Clean) {
        $AppBuildArgs.Clean = $true
    }
    if ($InstallPyInstaller) {
        $AppBuildArgs.InstallPyInstaller = $true
    }
    if ($PythonExe) {
        $AppBuildArgs.PythonExe = $PythonExe
    }
    & (Join-Path $ProjectRoot "package_mvp.ps1") @AppBuildArgs
}

$Iscc = Find-InnoSetupCompiler $InnoSetupCompiler
if (-not $Iscc) {
    throw "Inno Setup 6 compiler(ISCC.exe)를 찾지 못했습니다. https://jrsoftware.org/isinfo.php 에서 설치한 뒤 다시 실행하거나 -InnoSetupCompiler 경로를 지정하세요."
}

$InstallerScript = Join-Path $ProjectRoot "installer\windows\ClassInEDBMVP.iss"
& $Iscc $InstallerScript

$InstallerPath = Join-Path (Join-Path $ProjectRoot $OutputDir) "$AppName-Setup.exe"
Write-Host "Installer complete."
Write-Host "Installer: $InstallerPath"
