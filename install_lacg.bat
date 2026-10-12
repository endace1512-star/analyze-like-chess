@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Chess Studio: cai dat moi truong (Python 3.12 + GPU NVIDIA) ===
where py >nul 2>nul || (echo Chua co lenh "py". Cai Python 3.12 tu python.org, tick "Add python.exe to PATH". & pause & exit /b 1)
if not exist venv\Scripts\python.exe (
  py -3.12 -m venv venv || (echo Khong tao duoc venv bang Python 3.12 & pause & exit /b 1)
)
venv\Scripts\python -m pip install --upgrade pip
echo --- torch 2.8 ban CUDA 12.8 (khoang 3GB) ---
venv\Scripts\python -m pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128 || goto :err
echo --- whisperx, demucs, pycapcut, pillow, opencv ---
venv\Scripts\python -m pip install -r requirements_lacg.txt || goto :err
echo.
venv\Scripts\python lacg_check.py --sfx-dir sfx
echo.
echo Xong. Mo app bang file chess_studio.bat
pause
exit /b 0
:err
echo Cai dat LOI - doc thong bao phia tren.
pause
exit /b 1
