<#
.SYNOPSIS
    Removes Desk Control: the app, its shortcuts, the widget package and its signing certificate.
.PARAMETER KeepSettings
    Keep your presets and chosen desk (%APPDATA%\DeskControl).
#>
param([switch]$KeepSettings)
$ErrorActionPreference = 'Stop'
$dest = Join-Path $env:LOCALAPPDATA 'desk-control'
$subject = 'CN=DeskControl'

$pipe = New-Object System.IO.Pipes.NamedPipeClientStream('.', 'DeskControl', [System.IO.Pipes.PipeDirection]::InOut)
try {
    $pipe.Connect(500)
    $writer = New-Object System.IO.StreamWriter($pipe)
    $writer.AutoFlush = $true
    $writer.WriteLine('{"cmd": "quit"}')
    $null = (New-Object System.IO.StreamReader($pipe)).ReadLine()
    Start-Sleep -Seconds 3
} catch {
} finally {
    $pipe.Dispose()
}

Get-AppxPackage DeskControl.Widget | Remove-AppxPackage
foreach ($folder in 'Programs', 'Startup') {
    Remove-Item (Join-Path ([Environment]::GetFolderPath($folder)) 'Desk Control.lnk') -ErrorAction SilentlyContinue
}
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
if (-not $KeepSettings) { Remove-Item (Join-Path $env:APPDATA 'DeskControl') -Recurse -Force -ErrorAction SilentlyContinue }

Get-ChildItem Cert:\CurrentUser\My | Where-Object Subject -eq $subject | Remove-Item
$trusted = @(Get-ChildItem Cert:\LocalMachine\TrustedPeople | Where-Object Subject -eq $subject)
if ($trusted) {
    Write-Host 'Approve the UAC prompt to remove the Desk Control signing certificate from Trusted People.'
    $list = ($trusted.Thumbprint | ForEach-Object { "'$_'" }) -join ','
    Start-Process powershell -Verb RunAs -Wait -ArgumentList "-NoProfile -Command @($list) | ForEach-Object { Remove-Item Cert:\LocalMachine\TrustedPeople\`$_ }"
}

Write-Host 'Desk Control has been removed. To unpair the desk, remove it in Settings > Bluetooth & devices.'
