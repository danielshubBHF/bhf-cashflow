@echo off
rem Files unfiled NetSuite PDFs into the OneDrive-synced SharePoint project folders, then ticks Filed.
rem Scheduled daily by Windows Task Scheduler on a PC that syncs "6.0 Projects - Documents". Log: filing.log
rem Uses its own Python (%USERPROFILE%\.bhf-cashflow\venv) so a Python update on the PC can't break it. To rebuild:
rem   py -m venv "%USERPROFILE%\.bhf-cashflow\venv"
rem   "%USERPROFILE%\.bhf-cashflow\venv\Scripts\python.exe" -m pip install -r requirements.txt openpyxl python-dotenv
cd /d "%~dp0"
echo ==== %date% %time% >> filing.log
set "PY=%USERPROFILE%\.bhf-cashflow\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo FAILED: Python environment missing at %PY% - rebuild it with the two commands at the top of run_filing.cmd >> filing.log
  exit /b 1
)
rem New projects (a job folder with its costing): set them up from the costing, then file PDFs
"%PY%" -m bhf.setup_projects >> filing.log 2>&1 || echo FAILED: set-up ended with an error, see above >> filing.log
"%PY%" -m bhf.file_pdfs >> filing.log 2>&1 || echo FAILED: filing ended with an error, see above >> filing.log
