param([string]$Target = "$PSScriptRoot\distribution")
$ErrorActionPreference='Stop'
New-Item -ItemType Directory -Path $Target -Force | Out-Null
$csc="$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$out=Join-Path $Target "PNG Maker.exe"
$resources=@("index.html","app.js","core.js","names.js","demo.js","style.css") | ForEach-Object {
    $f=Join-Path "$PSScriptRoot\web" $_
    if (!(Test-Path -LiteralPath $f -PathType Leaf)) {throw "Missing app resource $_"}
    "/resource:$f,$_,private"
}
& $csc /nologo /optimize+ /target:winexe /platform:anycpu "/out:$out" /reference:System.Windows.Forms.dll /reference:System.Drawing.dll @resources "$PSScriptRoot\Launcher.cs"
if ($LASTEXITCODE -ne 0) {throw "Windows launcher compile failed ($LASTEXITCODE)"}
Copy-Item -LiteralPath "$PSScriptRoot\실행 안내.txt" -Destination $Target -Force
$proc=Start-Process -FilePath $out -ArgumentList '--self-test' -WindowStyle Hidden -Wait -PassThru
if($proc.ExitCode -ne 0) {throw "Windows launcher smoke failed ($($proc.ExitCode))"}
Write-Output "Embedded resource and HTTP smoke: PASS"
