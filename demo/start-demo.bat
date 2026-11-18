@echo off
REM Oversyte pitch demo: starts the demo dashboard and a public HTTPS link.
REM Keep this window open during the pitch. Closing it stops the demo and the link.
cd /d "%~dp0.."
start "Oversyte demo server" cmd /k ".venv\Scripts\python run.py --config demo\config.yaml"
timeout /t 8 /nobreak >nul
echo.
echo ================================================================
echo  Your public link appears below as https://....trycloudflare.com
echo  Password: oversyte2026
echo ================================================================
echo.
"C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://127.0.0.1:8090
