@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 访问口令（打开网页时要输入的密码），想改口令改这一行即可
set "DEFECT_PASSWORD=LM"

echo ============================================
echo   研发缺陷协作系统 ^| 后端版 ^| 启动中...
echo   启动后访问: http://127.0.0.1:8080
echo   访问口令: %DEFECT_PASSWORD%
echo   按 Ctrl+C 停止服务
echo ============================================

rem 自动探测可用的 Python（优先 PATH 中的 python，回退 Anaconda）
set "PY=python"
where python >nul 2>nul
if errorlevel 1 (
  if exist "D:\Anaconda\python.exe" (
    set "PY=D:\Anaconda\python.exe"
  ) else (
    echo [错误] 未找到 Python，请先安装并加入 PATH。
    pause
    exit /b 1
  )
)

rem 首次运行自动安装依赖
%PY% -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo [提示] 首次运行，正在安装依赖 fastapi / uvicorn ...
  %PY% -m pip install -r requirements.txt
)

%PY% server.py
pause
