<#
.SYNOPSIS
    Installs Desk Control for the current user.
.DESCRIPTION
    Creates a Python virtual environment in %LOCALAPPDATA%\desk-control, copies the app there, adds Start menu
    and sign-in shortcuts, then builds, signs and installs the Widgets board widget.
.PARAMETER SkipWidget
    Don't build or install the Widgets board widget (it needs the .NET 10 SDK).
.PARAMETER NoStartup
    Don't start Desk Control in the tray when you sign in to Windows.
#>
param([switch]$SkipWidget, [switch]$NoStartup)
$ErrorActionPreference = 'Stop'
$src = $PSScriptRoot
$dest = Join-Path $env:LOCALAPPDATA 'desk-control'
$venv = Join-Path $dest 'venv'
$py = Join-Path $venv 'Scripts\python.exe'
$pythonw = Join-Path $venv 'Scripts\pythonw.exe'

function Send-DeskCommand([string]$Json) {
    $pipe = New-Object System.IO.Pipes.NamedPipeClientStream('.', 'DeskControl', [System.IO.Pipes.PipeDirection]::InOut)
    try {
        $pipe.Connect(500)
        $writer = New-Object System.IO.StreamWriter($pipe)
        $writer.AutoFlush = $true
        $writer.WriteLine($Json)
        $null = (New-Object System.IO.StreamReader($pipe)).ReadLine()
        return $true
    } catch {
        return $false
    } finally {
        $pipe.Dispose()
    }
}

function New-Shortcut([string]$Folder, [string]$Arguments) {
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath($Folder)) 'Desk Control.lnk'))
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = "`"$dest\app\app.py`" $Arguments".Trim()
    $shortcut.WorkingDirectory = "$dest\app"
    $shortcut.Save()
}

New-Item "$dest\app" -ItemType Directory -Force | Out-Null
# Run tools from a local directory: cmd-based launchers can't start in a UNC path (e.g. a WSL checkout).
Push-Location $dest
try {
    if (-not (Test-Path $py)) {
        if (Get-Command py -ErrorAction SilentlyContinue) { $python = 'py'; $pythonArgs = @('-3') }
        elseif (Get-Command python -ErrorAction SilentlyContinue) { $python = 'python'; $pythonArgs = @() }
        else { throw 'Python 3.11 or newer is required. Install it with: winget install Python.Python.3.14' }
        & $python @pythonArgs -c 'import sys; sys.exit(sys.version_info < (3, 11))'
        if ($LASTEXITCODE) { throw 'Python 3.11 or newer is required. Install it with: winget install Python.Python.3.14' }
        & $python @pythonArgs -m venv $venv
        if ($LASTEXITCODE) { throw 'Could not create the Python virtual environment' }
    }
    & $py -m pip install --disable-pip-version-check -q -r "$src\requirements.txt"
    if ($LASTEXITCODE) { throw 'pip install failed' }
    # pip has been seen to leave PySide6\__init__.py out of a fresh venv; repair it if Qt won't import.
    & $py -c "try:`n import PySide6.QtCore`nexcept ImportError:`n raise SystemExit(1)"
    if ($LASTEXITCODE) {
        $pin = (Select-String -Path "$src\requirements.txt" -Pattern '^PySide6==(.+)$').Matches[0].Groups[1].Value
        & $py -m pip install --disable-pip-version-check -q --force-reinstall --no-deps "PySide6_Essentials==$pin"
    }

    $wasRunning = Send-DeskCommand '{"cmd": "quit"}'
    if ($wasRunning) { Start-Sleep -Seconds 3 }
    Copy-Item "$src\app.py", "$src\desk.py", "$src\ipc.py", "$src\icons.py", "$src\alexa_link.py" "$dest\app" -Force

    New-Shortcut 'Programs' ''
    $startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'Desk Control.lnk'
    if ($NoStartup) { Remove-Item $startup -ErrorAction SilentlyContinue } else { New-Shortcut 'Startup' '--background' }
    Write-Host "Installed Desk Control to $dest"

    if (-not $SkipWidget) {
        $sdks = if (Get-Command dotnet -ErrorAction SilentlyContinue) { & dotnet --list-sdks } else { @() }
        if ($sdks -match '^(1\d|[2-9]\d)\.') {
            $widgetSrc = Join-Path $dest 'widget-src'
            if (Test-Path $widgetSrc) { Remove-Item $widgetSrc -Recurse -Force }
            New-Item $widgetSrc -ItemType Directory | Out-Null
            Copy-Item "$src\widget\*.cs", "$src\widget\*.csproj", "$src\widget\AppxManifest.xml", "$src\widget\build.ps1", "$src\icons.py" $widgetSrc
            & "$widgetSrc\build.ps1" -Python $py
        } else {
            Write-Warning 'Skipped the widget: it needs the .NET 10 SDK (winget install Microsoft.DotNet.SDK.10).'
        }
    }
} finally {
    Pop-Location
}

Start-Process (Join-Path ([Environment]::GetFolderPath('Programs')) 'Desk Control.lnk')
