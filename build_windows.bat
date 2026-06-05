@echo off
chcp 65001 >nul
echo ============================================
echo   Audio Balancer - Windows 打包腳本
echo ============================================
echo.

:: 檢查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [錯誤] 找不到 Python，請先安裝 Python 3.10 以上版本
    echo 下載網址: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] 找到 Python:
python --version

:: 建立虛擬環境
echo.
echo [1/4] 建立虛擬環境...
if exist venv_win rmdir /s /q venv_win
python -m venv venv_win
if errorlevel 1 (
    echo [錯誤] 建立虛擬環境失敗
    pause
    exit /b 1
)
echo [OK] 虛擬環境建立完成

:: 安裝依賴套件
echo.
echo [2/4] 安裝依賴套件（可能需要幾分鐘）...
call venv_win\Scripts\activate.bat
pip install --upgrade pip --quiet
pip install customtkinter==5.2.2 pydub==0.25.1 pyloudnorm==0.2.0 numpy sounddevice soundfile Pillow pyinstaller --quiet
if errorlevel 1 (
    echo [錯誤] 安裝套件失敗，請確認網路連線
    pause
    exit /b 1
)
echo [OK] 套件安裝完成

:: 下載 ffmpeg（pydub 播放音檔需要）
echo.
echo [3/4] 確認 ffmpeg...
where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [提示] 系統未安裝 ffmpeg，嘗試自動下載...
    powershell -Command "& {Invoke-WebRequest -Uri 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip' -OutFile 'ffmpeg_tmp.zip' -UseBasicParsing}" >nul 2>&1
    if exist ffmpeg_tmp.zip (
        powershell -Command "Expand-Archive -Path ffmpeg_tmp.zip -DestinationPath ffmpeg_extracted -Force" >nul 2>&1
        for /d %%i in (ffmpeg_extracted\ffmpeg-*) do (
            if not exist ffmpeg mkdir ffmpeg
            copy "%%i\bin\ffmpeg.exe" ffmpeg\ >nul 2>&1
            copy "%%i\bin\ffprobe.exe" ffmpeg\ >nul 2>&1
        )
        del ffmpeg_tmp.zip >nul 2>&1
        rmdir /s /q ffmpeg_extracted >nul 2>&1
        echo [OK] ffmpeg 下載完成
    ) else (
        echo [警告] ffmpeg 下載失敗，部分音檔格式（mp3等）可能無法載入
        echo        可手動安裝: winget install Gyan.FFmpeg
    )
) else (
    echo [OK] 系統已安裝 ffmpeg
)

:: PyInstaller 打包
echo.
echo [4/4] 打包 .exe（可能需要 2-5 分鐘）...

set FFMPEG_BINARIES=
if exist ffmpeg\ffmpeg.exe set FFMPEG_BINARIES=--add-binary "ffmpeg\ffmpeg.exe;." --add-binary "ffmpeg\ffprobe.exe;."

pyinstaller --noconfirm --clean ^
    --windowed ^
    --name "Audio Balancer" ^
    --collect-all customtkinter ^
    --collect-all pyloudnorm ^
    --collect-all scipy ^
    --hidden-import pydub ^
    --hidden-import sounddevice ^
    --hidden-import soundfile ^
    --hidden-import importlib.resources ^
    --hidden-import importlib.metadata ^
    %FFMPEG_BINARIES% ^
    audio_balancer.py

if errorlevel 1 (
    echo.
    echo [錯誤] 打包失敗，請查看上方錯誤訊息
    pause
    exit /b 1
)

echo.
echo ============================================
echo   打包完成！
echo   檔案位置: dist\Audio Balancer\Audio Balancer.exe
echo ============================================
echo.
echo 可以將整個 dist\Audio Balancer\ 資料夾複製給對方使用。
echo.
pause
