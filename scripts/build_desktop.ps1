param([switch]$InstallShortcut)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$version = '1.0.4258.31'
$expectedHash = '56F7F4B8BF9AEE4B8EFEFBBDD4F67D5F74EBD1B100ED0806DA71BF76AF481AA9'
$packageDir = Join-Path $projectRoot "build\tools\webview2-$version"
$archive = Join-Path $packageDir 'sdk.zip'
$sdk = Join-Path $packageDir 'sdk'
$outputDir = Join-Path $projectRoot 'build\desktop'
$executable = Join-Path $outputDir 'SensorLab.exe'
$pending = Join-Path $outputDir 'SensorLab.pending.exe'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw 'The Windows .NET Framework C# compiler is required.' }
New-Item -ItemType Directory -Force -Path $packageDir, $outputDir | Out-Null
if (-not (Test-Path -LiteralPath $archive)) {
    Invoke-WebRequest -UseBasicParsing "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$version/microsoft.web.webview2.$version.nupkg" -OutFile $archive
}
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expectedHash) {
    throw 'WebView2 SDK checksum mismatch. The archive was not extracted or used.'
}
Expand-Archive -LiteralPath $archive -DestinationPath $sdk -Force
foreach ($name in @('Microsoft.Web.WebView2.Core.dll', 'Microsoft.Web.WebView2.WinForms.dll')) {
    Copy-Item -LiteralPath (Join-Path $sdk "lib\net462\$name") -Destination $outputDir
}
Copy-Item -LiteralPath (Join-Path $sdk 'runtimes\win-x64\native\WebView2Loader.dll') -Destination $outputDir
Copy-Item -LiteralPath (Join-Path $sdk 'LICENSE.txt') -Destination (Join-Path $outputDir 'WebView2-LICENSE.txt')
$arguments = @(
    '/nologo', '/target:winexe', '/platform:x64', '/optimize+', '/warn:4', '/warnaserror+',
    "/out:$pending",
    '/reference:System.dll', '/reference:System.Core.dll', '/reference:System.Drawing.dll',
    '/reference:System.Windows.Forms.dll', '/reference:System.Net.Http.dll', '/reference:System.Web.Extensions.dll',
    "/reference:$outputDir\Microsoft.Web.WebView2.Core.dll",
    "/reference:$outputDir\Microsoft.Web.WebView2.WinForms.dll",
    (Join-Path $projectRoot 'desktop\SensorLab.cs')
)
& $compiler $arguments
if ($LASTEXITCODE -ne 0) { throw "Desktop compilation failed: $LASTEXITCODE" }
# Compile before replacing the working app. Preserve prior binaries for rollback.
# Renaming also avoids truncating a binary still held by a scanner.
if (Test-Path -LiteralPath $executable) {
    $archiveDir = Join-Path $outputDir 'previous'
    $previous = Join-Path $archiveDir ("SensorLab-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.exe')
    foreach ($moveTarget in @($executable, $previous, $pending)) {
        if (-not ([IO.Path]::GetFullPath($moveTarget).StartsWith(
            [IO.Path]::GetFullPath($outputDir) + '\', [StringComparison]::OrdinalIgnoreCase))) {
            throw 'Build replacement path escapes the desktop output directory.'
        }
    }
    New-Item -ItemType Directory -Force -Path $archiveDir | Out-Null
    Move-Item -LiteralPath $executable -Destination $previous
}
Move-Item -LiteralPath $pending -Destination $executable
@'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <startup><supportedRuntime version="v4.0" sku=".NETFramework,Version=v4.8"/></startup>
  <runtime><AppContextSwitchOverrides value="Switch.System.Windows.Forms.DoNotSupportSelectAllShortcutInMultilineTextBox=false"/></runtime>
</configuration>
'@ | Set-Content -LiteralPath (Join-Path $outputDir 'SensorLab.exe.config') -Encoding UTF8
if ($InstallShortcut) {
    $desktopDir = [Environment]::GetFolderPath('Desktop')
    $shellLink = New-Object -ComObject WScript.Shell
    $shortcut = $shellLink.CreateShortcut((Join-Path $desktopDir 'STM32 Sensor Lab.lnk'))
    $shortcut.TargetPath = Join-Path $outputDir 'SensorLab.exe'
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.Description = 'STM32 Sensor Lab: live Renode telemetry, component tests and saved reports'
    $shortcut.IconLocation = "$(Join-Path $outputDir 'SensorLab.exe'),0"
    $shortcut.Save()
}
Write-Output "Built: $outputDir\SensorLab.exe"
