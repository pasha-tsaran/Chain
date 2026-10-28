@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Первичная установка моделей. Потребуется интернет и несколько ГБ на диске.
python bootstrap.py
pause
