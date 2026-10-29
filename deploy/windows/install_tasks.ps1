# Registers the two scheduled tasks for the AI backend host. Setup steps: docs/DEPLOY_WINDOWS.md
# Run once in an elevated (Administrator) PowerShell from the project folder:
#   powershell -ExecutionPolicy Bypass -File deploy\windows\install_tasks.ps1
#
# - "CourseCompass Backend": starts run_backend.bat at boot, whether or not anyone is logged on.
#   Its time limit is removed: Task Scheduler stops tasks after 72 hours by default, which would
#   kill the server every three days. (schtasks.exe cannot change that limit, hence PowerShell.)
# - "CourseCompass Update": runs update.bat daily at 04:30.
# Running it again replaces both tasks (-Force).
# Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less files with the ANSI codepage.

$ErrorActionPreference = "Stop"
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$user = "$env:USERDOMAIN\$env:USERNAME"
$cred = Get-Credential -UserName $user -Message "Windows password for $user (lets the tasks run while nobody is logged on)"
$password = $cred.GetNetworkCredential().Password

$backendAction = New-ScheduledTaskAction -Execute "$root\deploy\windows\run_backend.bat" -WorkingDirectory $root
$backendSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "CourseCompass Backend" -Action $backendAction `
    -Trigger (New-ScheduledTaskTrigger -AtStartup) -Settings $backendSettings `
    -User $cred.UserName -Password $password -RunLevel Limited -Force | Out-Null

$updateAction = New-ScheduledTaskAction -Execute "$root\deploy\windows\update.bat" -WorkingDirectory $root
$updateSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "CourseCompass Update" -Action $updateAction `
    -Trigger (New-ScheduledTaskTrigger -Daily -At "04:30") -Settings $updateSettings `
    -User $cred.UserName -Password $password -RunLevel Limited -Force | Out-Null

Start-ScheduledTask -TaskName "CourseCompass Backend"
Write-Host "Registered both tasks and started the backend (port: API_PORT in $root\.env)."
Write-Host "Check with:  Get-ScheduledTask -TaskName 'CourseCompass*' | Get-ScheduledTaskInfo"
