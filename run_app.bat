@echo off
cd /d "%~dp0"
echo Starting the screener... keep this window open while you use it.
"%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" -m streamlit run app.py
pause
