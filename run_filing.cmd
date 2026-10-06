@echo off
rem Files unfiled NetSuite PDFs into the OneDrive-synced SharePoint project folders, then ticks Filed.
rem Scheduled daily by Windows Task Scheduler on a PC that syncs "6.0 Projects - Documents". Log: filing.log
cd /d "%~dp0"
echo ==== %date% %time% >> filing.log
python -m bhf.file_pdfs >> filing.log 2>&1
