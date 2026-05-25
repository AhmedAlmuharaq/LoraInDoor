# InDoorLora — Full Setup Script
# Installs: Miniconda, GNU Radio, UHD, gr-lora, all Python deps
# Run as Administrator: Right-click PowerShell → "Run as Administrator"
# Then: Set-ExecutionPolicy RemoteSigned; .\setup.ps1

$ErrorActionPreference = "Continue"
$PROJECT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
$CONDA_DIR   = "$env:USERPROFILE\miniconda3"
$ENV_NAME    = "indoorlora"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  InDoorLora — Full Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Miniconda ─────────────────────────────────────────────────
Write-Host "[1/5] Checking Conda..." -ForegroundColor Yellow
$condaExe = "$CONDA_DIR\Scripts\conda.exe"

if (-not (Test-Path $condaExe)) {
    Write-Host "  Downloading Miniconda3..." -ForegroundColor Yellow
    $installer = "$env:TEMP\Miniconda3-latest.exe"
    Invoke-WebRequest `
        -Uri "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe" `
        -OutFile $installer `
        -UseBasicParsing
    Write-Host "  Installing Miniconda3 to $CONDA_DIR ..." -ForegroundColor Yellow
    Start-Process -FilePath $installer `
        -ArgumentList "/S /D=$CONDA_DIR" `
        -Wait -NoNewWindow
    Write-Host "  Miniconda installed." -ForegroundColor Green
} else {
    Write-Host "  Conda already installed." -ForegroundColor Green
}

$env:PATH = "$CONDA_DIR\Scripts;$CONDA_DIR\condabin;$CONDA_DIR;$env:PATH"

# ── Step 2: Create conda environment ─────────────────────────────────
Write-Host ""
Write-Host "[2/5] Creating conda environment '$ENV_NAME'..." -ForegroundColor Yellow
& $condaExe create -n $ENV_NAME python=3.10 -y --quiet 2>&1 | Out-Null
Write-Host "  Environment ready." -ForegroundColor Green

$condaPython = "$CONDA_DIR\envs\$ENV_NAME\python.exe"
$condaPip    = "$CONDA_DIR\envs\$ENV_NAME\Scripts\pip.exe"

# ── Step 3: GNU Radio + UHD ───────────────────────────────────────────
Write-Host ""
Write-Host "[3/5] Installing GNU Radio + UHD (large download, ~1 GB)..." -ForegroundColor Yellow
Write-Host "  This will take 10-20 minutes depending on internet speed." -ForegroundColor Gray
& $condaExe install -n $ENV_NAME -c conda-forge gnuradio uhd -y 2>&1 | Tee-Object -Variable gnuradioOut
if ($LASTEXITCODE -eq 0) {
    Write-Host "  GNU Radio + UHD installed." -ForegroundColor Green
} else {
    Write-Host "  WARNING: GNU Radio install had issues. Trying minimal install..." -ForegroundColor Red
    & $condaExe install -n $ENV_NAME -c conda-forge uhd -y 2>&1 | Out-Null
}

# ── Step 4: gr-lora ───────────────────────────────────────────────────
Write-Host ""
Write-Host "[4/5] Installing gr-lora (LoRa demodulator)..." -ForegroundColor Yellow
$loraOk = $false

# Try conda-forge first
& $condaExe install -n $ENV_NAME -c conda-forge gnuradio-lora -y 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  gr-lora installed via conda-forge." -ForegroundColor Green
    $loraOk = $true
}

if (-not $loraOk) {
    # Try pip
    & $condaPip install lora-craft 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  lora-craft installed via pip." -ForegroundColor Green
        $loraOk = $true
    }
}

if (-not $loraOk) {
    Write-Host "  gr-lora not found in package repos." -ForegroundColor Yellow
    Write-Host "  The USRP bridge will use the built-in Python demodulator." -ForegroundColor Yellow
}

# ── Step 5: Python packages ───────────────────────────────────────────
Write-Host ""
Write-Host "[5/5] Installing Python packages..." -ForegroundColor Yellow
& $condaPip install pyzmq pyserial numpy scipy 2>&1 | Out-Null
Write-Host "  pyzmq, pyserial, numpy, scipy installed." -ForegroundColor Green

# ── Download UHD FPGA images (for USRP B-series) ─────────────────────
Write-Host ""
Write-Host "[+] Downloading UHD FPGA images for USRP..." -ForegroundColor Yellow
$uhdImagesDl = "$CONDA_DIR\envs\$ENV_NAME\Library\bin\uhd_images_downloader.exe"
if (Test-Path $uhdImagesDl) {
    & $uhdImagesDl 2>&1 | Tee-Object -Variable imgOut
    Write-Host "  FPGA images downloaded." -ForegroundColor Green
} else {
    # Try python version
    & $condaPython -m uhd.uhd_images_downloader 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  FPGA images downloaded." -ForegroundColor Green
    } else {
        Write-Host "  Could not auto-download FPGA images." -ForegroundColor Yellow
        Write-Host "  Run manually: uhd_images_downloader" -ForegroundColor Yellow
    }
}

# ── Create activation batch file ─────────────────────────────────────
$batContent = @"
@echo off
call "$CONDA_DIR\Scripts\activate.bat" $ENV_NAME
echo.
echo InDoorLora environment active.
echo.
echo Commands:
echo   python positioning_server.py        -- Real hardware mode
echo   python positioning_server.py --sim  -- Simulation mode
echo   python usrp_lora_bridge.py          -- USRP receiver bridge
echo   python serial_bridge.py             -- Feather M0 receiver bridge
echo.
"@
$batContent | Out-File -FilePath "$PROJECT_DIR\run_indoorlora.bat" -Encoding ASCII
Write-Host ""
Write-Host "  Created run_indoorlora.bat" -ForegroundColor Green

# ── Done ──────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Setup Complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Double-click run_indoorlora.bat  (opens activated terminal)"
Write-Host "  2. python positioning_server.py     (start server)"
Write-Host "  3. python usrp_lora_bridge.py       (start USRP receiver)"
Write-Host "  4. Open InDoorLora_Dashboard.html   (in browser)"
Write-Host ""
Write-Host "Test USRP detected:" -ForegroundColor Cyan
Write-Host "  uhd_find_devices"
Write-Host ""
