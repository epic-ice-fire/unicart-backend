$ErrorActionPreference = "Stop"

$source = Join-Path $env:USERPROFILE "Desktop\unicart_app"
$baseDest = Join-Path $env:USERPROFILE "Desktop\unicart-backend-clean"
$repo = "https://github.com/epic-ice-fire/unicart-backend.git"

if (-not (Test-Path $source)) {
    throw "Source project not found: $source"
}

# Never overwrite a previous clean clone. Pick the next free name automatically.
$dest = $baseDest
$counter = 2
while (Test-Path $dest) {
    $dest = "$baseDest-$counter"
    $counter++
}

Write-Host "Cloning backend repository..."
Write-Host "Destination: $dest"
git clone $repo $dest
if ($LASTEXITCODE -ne 0) { throw "git clone failed" }

Write-Host "Removing stale frontend-only files from the BACKEND repository clone..."

$frontendPaths = @(
    "lib",
    "android",
    "ios",
    "web",
    "windows",
    "linux",
    "macos",
    ".dart_tool",
    "build",
    "test",
    "pubspec.yaml",
    "pubspec.lock",
    "analysis_options.yaml"
)

foreach ($relativePath in $frontendPaths) {
    $target = Join-Path $dest $relativePath
    if (Test-Path $target) {
        Remove-Item $target -Recurse -Force
        Write-Host "  removed $relativePath"
    }
}

Write-Host "Copying the CURRENT hardened backend from unicart_app..."

robocopy (Join-Path $source "app") (Join-Path $dest "app") /E /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -ge 8) { throw "Failed to copy app/" }

if (Test-Path (Join-Path $source "scripts")) {
    robocopy (Join-Path $source "scripts") (Join-Path $dest "scripts") /E /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) { throw "Failed to copy scripts/" }
}

if (Test-Path (Join-Path $source "tests")) {
    robocopy (Join-Path $source "tests") (Join-Path $dest "tests") /E /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) { throw "Failed to copy tests/" }
}

foreach ($name in @(".gitignore", ".env.example", "requirements.txt")) {
    $src = Join-Path $source $name
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $dest $name) -Force
    }
}

# Deliberately DO NOT copy:
# - .env or any real secret file
# - unicart.db or other local databases
# - backups
# - Flutter frontend source
# - build output
Set-Location $dest

Write-Host ""
Write-Host "Checking that sensitive local files were not copied..."

$forbidden = @(
    ".env",
    "unicart.db",
    "unicart.db-shm",
    "unicart.db-wal"
)

foreach ($name in $forbidden) {
    if (Test-Path (Join-Path $dest $name)) {
        throw "Sensitive/local file unexpectedly exists in clean backend clone: $name"
    }
}

Write-Host "Running security tests from the backend-only clean Git clone..."
python -m pytest tests/test_financial_integrity.py tests/test_authorization_security.py tests/test_checkout_recovery.py tests/test_session_security.py -q
if ($LASTEXITCODE -ne 0) {
    throw "Security tests failed. Do not commit/push yet."
}

Write-Host ""
Write-Host "Security tests passed."
Write-Host ""
Write-Host "Prepared clean backend repository:"
Write-Host $dest
Write-Host ""
Write-Host "Review the exact Git changes with:"
Write-Host "  git status --short"
Write-Host "  git diff"
Write-Host ""
Write-Host "The deleted stale Flutter files SHOULD appear as deletions."
Write-Host ""
Write-Host "After review, stage all sanitized changes including deletions:"
Write-Host "  git add -A"
Write-Host '  git commit -m "Security hardening and production preparation"'
Write-Host "  git push"
