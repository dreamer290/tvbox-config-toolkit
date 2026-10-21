@echo off
rem 重新打包 TVBox 配置工具（需要联网安装 PyInstaller，仅首次）
cd /d "%~dp0"
if not exist .venv-build\Scripts\python.exe (
    echo 首次构建：创建虚拟环境并安装 PyInstaller ...
    py -3.12 -m venv .venv-build || python -m venv .venv-build
    .venv-build\Scripts\python.exe -m pip install pyinstaller
)
.venv-build\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name TVBoxConfigTool TVBoxTool.py
echo.
echo 完成：dist\TVBoxConfigTool.exe
pause
