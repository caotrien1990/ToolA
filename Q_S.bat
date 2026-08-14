@echo off
REM STT-3: prefer CLAUDE_MEM_HOME, else the D-root ToolQ Q_S.bat.
REM Do NOT default to the legacy C root %USERPROFILE%\.claude-mem.
if defined CLAUDE_MEM_HOME (
  call "%CLAUDE_MEM_HOME%\Q_S.bat"
) else (
  call "D:\0. TRIEN\1. DATA\DATA\5. VIBE APP\1. APP\21. ToolQ\Q_S.bat"
)
exit /b %ERRORLEVEL%
