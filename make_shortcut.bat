@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) '채권간편검색기.lnk')); $s.TargetPath=(Join-Path $PWD 'lookup.bat'); $s.WorkingDirectory=$PWD.Path; $s.IconLocation=(Join-Path $PWD 'bond_icon.ico'); $s.WindowStyle=7; $s.Save()"
echo 바탕화면에 [채권간편검색기] 바로가기를 만들었습니다.
pause
