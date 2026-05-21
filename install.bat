@echo off
chcp 65001 > nul
echo.
echo  ╔══════════════════════════════════════╗
echo  ║        VOLUME_X  설치 스크립트        ║
echo  ╚══════════════════════════════════════╝
echo.

:: Python 확인
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  [오류] Python 을 찾을 수 없습니다.
    echo         https://www.python.org/downloads/
    echo         Python 3.10 이상을 설치하세요.
    pause
    exit /b 1
)

echo  [1/3] pip 업그레이드 중...
python -m pip install --upgrade pip --quiet

echo  [2/3] 패키지 설치 중...
pip install -r requirements.txt

echo  [3/3] 설치 확인 중...
python -c "import PyQt5, pyvista, trimesh, numpy, scipy; print('  모든 패키지 OK')"

if %errorlevel% neq 0 (
    echo  [경고] 일부 패키지 설치에 실패했습니다.
    echo         오류 메시지를 확인하고 수동으로 설치하세요.
) else (
    echo.
    echo  ✓ 설치 완료!
    echo    run.bat 을 실행하여 VOLUME_X 를 시작하세요.
)

echo.
pause
