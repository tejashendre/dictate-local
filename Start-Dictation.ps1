# PowerShell launcher.
#
# PowerShell refuses to run a file in the current directory by bare name, so
# "Dictate.cmd" fails with "not recognized" even though the file is right
# there. That is PowerShell policy, not a missing file.
#
# Run this with:    .\Start-Dictation.ps1
# Or just double-click Dictate.cmd in File Explorer, which has no such rule.

Set-Location -Path $PSScriptRoot
Write-Host ""
Write-Host "  Local Dictation" -ForegroundColor Green
Write-Host ""
Write-Host "    F9                  start / stop talking"
Write-Host "    drag the pill       move it anywhere"
Write-Host "    right-click pill    settings"
Write-Host ""
python dictate.py
