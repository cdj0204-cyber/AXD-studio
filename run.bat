@echo off
chcp 65001 > nul
echo  VOLUME_X 시작 중...
python main.py
if %errorlevel% neq 0 (
    echo.
    echo  [오류] 실행에 실패했습니다.
    echo         install.bat 을 먼저 실행하세요.
    pause
)
