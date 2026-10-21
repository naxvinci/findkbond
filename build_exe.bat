@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   나봉검색기 실행 파일(exe) 만들기
echo   (이 작업은 한 번만 하시면 됩니다. 몇 분 걸립니다)
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [1/3] 파이썬이 없어서 설치합니다...
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo.
        echo 자동 설치에 실패했습니다. https://www.python.org/downloads/ 에서 파이썬을 설치해 주세요.
        echo 설치할 때 "Add Python to PATH"를 꼭 체크해 주세요. 그 다음 이 파일을 다시 실행해 주세요.
        pause
        exit /b 1
    )
    echo.
    echo 파이썬 설치가 끝났습니다. 이 창을 닫고 build_exe.bat 을 한 번 더 실행해 주세요.
    pause
    exit /b 0
)

echo [1/3] 파이썬 확인 완료
echo [2/3] 빌드 도구 설치 중...
python -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
    echo 빌드 도구 설치에 실패했습니다. 인터넷 연결을 확인해 주세요.
    pause
    exit /b 1
)

echo [3/3] exe 만드는 중...
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name BondSearch --icon bond_icon.ico --add-data "bond_icon.ico;." bond_uw_gui.py
if errorlevel 1 (
    echo 빌드에 실패했습니다. 위 오류 내용을 알려 주세요.
    pause
    exit /b 1
)

copy /y "dist\BondSearch.exe" "나봉검색기.exe" >nul
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q BondSearch.spec 2>nul

echo.
echo 완료! 이 폴더에 [나봉검색기.exe] 가 만들어졌습니다.
echo 이 파일 하나만 다른 분께 전달하시면 됩니다. (파이썬 설치 불필요)
pause
