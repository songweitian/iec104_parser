@echo off
chcp 65001 >nul
echo =====================================
echo  IEC104 Parser - 构建脚本
echo  构建时间: %date% %time%
echo =====================================
echo.

REM 检测Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

REM 校验 Python 主版本 >= 3.8（L4 修复）
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [错误] Python 版本过低，本项目需要 Python 3.8+（当前版本见下方）
    python --version
    pause
    exit /b 1
)

REM 检测并安装依赖（统一按 requirements.txt 安装，避免版本漂移）
python -c "import PyQt5, cryptography" >nul 2>&1
if errorlevel 1 (
    echo [安装] 正在安装依赖（PyQt5 / cryptography）...
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)

REM 检测PyInstaller
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [安装] 正在安装 PyInstaller...
    pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
)

REM 构建
echo [构建] 正在创建可执行文件...
REM 注意：不要用 --add-data 打包源码目录（会把 gen_license.py 签码密钥等带进发行包）
python -m PyInstaller ^
    --name="IEC104_Parser" ^
    --onedir ^
    --windowed ^
    --icon=assets\app.ico ^
    --exclude-module gen_license ^
    --noconfirm ^
    --clean ^
    main.py

if errorlevel 1 (
    echo.
    echo [错误] 构建失败！
    pause
    exit /b 1
)

echo.
echo =====================================
echo  构建完成！
echo  可执行文件位置:
echo  dist\IEC104_Parser\IEC104_Parser.exe
echo =====================================
echo.
echo 按任意键打开输出目录...
pause >nul
start "" "dist"
