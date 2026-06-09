@echo off
chcp 65001 >nul
powershell -ExecutionPolicy Bypass -File ".\copy_memos.ps1" %*
