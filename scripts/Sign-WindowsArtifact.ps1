function Find-EDBSignTool {
    param([string]$Requested)

    if ($Requested -and (Test-Path $Requested)) {
        return (Resolve-Path $Requested).Path
    }

    $Command = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if ($Command) {
        return $Command.Source
    }

    $CandidateRoots = @()
    if (${env:ProgramFiles(x86)}) {
        $CandidateRoots += (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin")
    }
    if ($env:ProgramFiles) {
        $CandidateRoots += (Join-Path $env:ProgramFiles "Windows Kits\10\bin")
    }

    foreach ($Root in $CandidateRoots) {
        if (-not (Test-Path $Root)) {
            continue
        }
        $Candidate = Get-ChildItem -Path $Root -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($Candidate) {
            return $Candidate.FullName
        }
    }

    return ""
}

function Invoke-EDBWindowsSignature {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$SignTool = "",
        [string]$CertificatePath = "",
        [string]$CertificatePassword = "",
        [string]$CertificateSubject = "",
        [string]$CertificateThumbprint = "",
        [switch]$CertificateAutoSelect,
        [string]$TimestampUrl = "http://timestamp.digicert.com",
        [string]$Description = "ClassIn EDB"
    )

    $ResolvedPath = if ([System.IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path (Get-Location) $Path }
    if (-not (Test-Path $ResolvedPath)) {
        throw "Signing target was not found: $ResolvedPath"
    }

    $SignToolPath = Find-EDBSignTool -Requested $SignTool
    if (-not $SignToolPath) {
        throw "signtool.exe was not found. Install the Windows SDK or pass -SignTool with the full signtool.exe path."
    }

    $SignArgs = @("sign", "/fd", "SHA256", "/td", "SHA256")
    if ($TimestampUrl) {
        $SignArgs += @("/tr", $TimestampUrl)
    }
    if ($Description) {
        $SignArgs += @("/d", $Description)
    }

    if ($CertificatePath) {
        $ResolvedCertificatePath = if ([System.IO.Path]::IsPathRooted($CertificatePath)) { $CertificatePath } else { Join-Path (Get-Location) $CertificatePath }
        if (-not (Test-Path $ResolvedCertificatePath)) {
            throw "Code-signing certificate was not found: $ResolvedCertificatePath"
        }
        $SignArgs += @("/f", $ResolvedCertificatePath)
        if ($CertificatePassword) {
            $SignArgs += @("/p", $CertificatePassword)
        }
    } elseif ($CertificateThumbprint) {
        $SignArgs += @("/sha1", $CertificateThumbprint)
    } elseif ($CertificateSubject) {
        $SignArgs += @("/n", $CertificateSubject)
        if ($CertificateAutoSelect) {
            $SignArgs += "/a"
        }
    } elseif ($CertificateAutoSelect) {
        $SignArgs += "/a"
    } else {
        throw "Windows signing requires -SignCertificatePath, -SignCertificateThumbprint, -SignCertificateSubject, or -SignCertificateAutoSelect."
    }

    $SignArgs += @("/v", $ResolvedPath)
    & $SignToolPath @SignArgs
    if ($LASTEXITCODE -ne 0) {
        throw "signtool sign failed for $ResolvedPath"
    }

    & $SignToolPath verify /pa /v $ResolvedPath
    if ($LASTEXITCODE -ne 0) {
        throw "signtool verify failed for $ResolvedPath"
    }
}

function Invoke-EDBWindowsPackageSigning {
    param(
        [Parameter(Mandatory = $true)][string]$PackagePath,
        [string]$SignTool = "",
        [string]$CertificatePath = "",
        [string]$CertificatePassword = "",
        [string]$CertificateSubject = "",
        [string]$CertificateThumbprint = "",
        [switch]$CertificateAutoSelect,
        [string]$TimestampUrl = "http://timestamp.digicert.com",
        [string]$Description = "ClassIn EDB"
    )

    $ResolvedPackagePath = if ([System.IO.Path]::IsPathRooted($PackagePath)) { $PackagePath } else { Join-Path (Get-Location) $PackagePath }
    if (-not (Test-Path $ResolvedPackagePath)) {
        throw "Package path was not found: $ResolvedPackagePath"
    }

    if ((Get-Item $ResolvedPackagePath).PSIsContainer) {
        $Targets = Get-ChildItem -Path $ResolvedPackagePath -Recurse -File |
            Where-Object { $_.Extension -in @(".exe", ".dll", ".pyd") } |
            Sort-Object FullName
    } else {
        $Targets = @(Get-Item $ResolvedPackagePath)
    }

    if (-not $Targets -or $Targets.Count -eq 0) {
        throw "No signable Windows artifacts were found under: $ResolvedPackagePath"
    }

    foreach ($Target in $Targets) {
        Invoke-EDBWindowsSignature `
            -Path $Target.FullName `
            -SignTool $SignTool `
            -CertificatePath $CertificatePath `
            -CertificatePassword $CertificatePassword `
            -CertificateSubject $CertificateSubject `
            -CertificateThumbprint $CertificateThumbprint `
            -CertificateAutoSelect:$CertificateAutoSelect `
            -TimestampUrl $TimestampUrl `
            -Description $Description
    }
}
