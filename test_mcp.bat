@echo off
title Project Orchestrator - MCP Test
echo.
echo  ==========================================
echo   MCP Stdio Mode Test
echo   Dung de kiem tra MCP handshake
echo   Nhan Ctrl+C de thoat
echo  ==========================================
echo.
cd /d "%~dp0"
call venv312\Scripts\activate
python orchestrator_server.py --mcp
pause
