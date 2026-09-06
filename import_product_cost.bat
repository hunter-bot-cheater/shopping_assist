@echo off
title Import product_cost
cd /d "%~dp0"

echo.
echo ============================================
echo   Import product_cost to MySQL "shop_data"
echo   File: product_cost_export.sql (must be in
echo         the SAME folder as this .bat)
echo ============================================
echo.

mysql --default-character-set=utf8mb4 -uroot -p shop_data < product_cost_export.sql

echo.
if %errorlevel%==0 (
    echo [OK] Import finished. Verify: SELECT COUNT(*) FROM product_cost;
) else (
    echo [FAIL] Import failed. See message above.
    echo If it says 'mysql' is not recognized, open this file and replace
    echo "mysql" with the full path to mysql.exe, e.g.:
    echo   "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysql"
)
echo.
pause
