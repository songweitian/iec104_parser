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

REM 检测依赖
python -c "import PyQt5" >nul 2>&1
if errorlevel 1 (
    echo [安装] 正在安装依赖...
    pip install PyQt5 -i https://pypi.tuna.tsinghua.edu.cn/simple
)

REM 检测PyInstaller
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [安装] 正在安装 PyInstaller...
    pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
)

REM 构建
echo [构建] 正在创建可执行文件...
python -m PyInstaller ^
    --name="IEC104_Parser" ^
    --onedir ^
    --windowed ^
    --icon=NONE ^
    --add-data=".;." ^
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
echo  dist\IEC104_Parser.exe
echo =====================================
echo.
echo 按任意键打开输出目录...
pause >nul
start "" "dist"
