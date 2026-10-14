# Builds, signs and installs the Desk Control widget package (MSIX) for the Windows 11 Widgets board.
param(
    [Parameter(Mandatory)] [string]$Python,  # venv python with PySide6, used to draw the icons
    [string]$Work = "$env:LOCALAPPDATA\desk-control\widget-build"
)
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$stage = Join-Path $Work 'stage'
$msix = Join-Path $Work 'DeskWidget.msix'
$nuget = Join-Path $env:USERPROFILE '.nuget\packages'
$subject = 'CN=DeskControl'

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
dotnet publish "$src\DeskWidget.csproj" -c Release -o $stage --nologo -v q
if ($LASTEXITCODE) { throw 'dotnet publish failed' }

# icons.py sits next to this script in the installer's copy, and one level up in the repository.
$icons = @("$src\icons.py", "$src\..\icons.py") | Where-Object { Test-Path $_ } | Select-Object -First 1
& $Python $icons "$stage\Assets"
if ($LASTEXITCODE) { throw 'icon generation failed' }
New-Item "$stage\Public" -ItemType Directory -Force | Out-Null
Set-Content "$stage\Public\README.txt" 'Desk Control widget'

# The package must depend on the same Windows App Runtime the SDK was built against.
$sdkVersion = ([xml](Get-Content "$src\DeskWidget.csproj")).Project.ItemGroup.PackageReference |
    Where-Object Include -eq 'Microsoft.WindowsAppSDK' | ForEach-Object Version
$versionInfo = Get-Content "$nuget\microsoft.windowsappsdk.runtime\$sdkVersion\include\WindowsAppSDK-VersionInfo.h" -Raw
$runtime = [regex]::Match($versionInfo, 'WINDOWSAPPSDK_RUNTIME_VERSION_DOTQUADSTRING\s+"([\d.]+)"').Groups[1].Value
$now = Get-Date
$version = '1.0.{0}.{1}' -f $now.ToString('yyMM'), ($now.Day * 1440 + $now.Hour * 60 + $now.Minute)
(Get-Content "$src\AppxManifest.xml" -Raw).Replace('$VERSION$', $version).Replace('$RUNTIME_VERSION$', $runtime) |
    Set-Content "$stage\AppxManifest.xml" -Encoding utf8

$tools = Get-ChildItem "$nuget\microsoft.windows.sdk.buildtools" -Recurse -Filter makeappx.exe |
    Where-Object FullName -like '*\x64\*' | Sort-Object FullName -Descending | Select-Object -First 1 | ForEach-Object DirectoryName
& "$tools\makeappx.exe" pack /o /d $stage /p $msix | Out-Null
if ($LASTEXITCODE) { throw 'makeappx failed' }

$cert = Get-ChildItem Cert:\CurrentUser\My |
    Where-Object { $_.Subject -eq $subject -and $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date) } | Select-Object -First 1
if (-not $cert) {
    $cert = New-SelfSignedCertificate -Type Custom -Subject $subject -KeyUsage DigitalSignature -KeyExportPolicy NonExportable `
        -FriendlyName 'Desk Control widget signing' -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears(5) `
        -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3', '2.5.29.19={text}')
}
if (-not (Get-ChildItem Cert:\LocalMachine\TrustedPeople | Where-Object Thumbprint -eq $cert.Thumbprint)) {
    $cer = Join-Path $Work 'DeskControl.cer'
    Export-Certificate -Cert $cert -FilePath $cer | Out-Null
    Write-Host 'Approve the UAC prompt to trust the Desk Control signing certificate on this PC.'
    Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile -Command Import-Certificate -FilePath '$cer' -CertStoreLocation Cert:\LocalMachine\TrustedPeople"
    if (-not (Get-ChildItem Cert:\LocalMachine\TrustedPeople | Where-Object Thumbprint -eq $cert.Thumbprint)) {
        throw 'Signing certificate was not trusted'
    }
}
& "$tools\signtool.exe" sign /q /fd SHA256 /sha1 $cert.Thumbprint $msix
if ($LASTEXITCODE) { throw 'signtool failed' }

Add-AppxPackage -Path $msix -ForceApplicationShutdown -ForceUpdateFromAnyVersion
Write-Host "Installed Desk Control widget $version"
