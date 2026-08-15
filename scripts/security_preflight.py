from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import settings

FAILURES: list[str] = []
WARNINGS: list[str] = []

SECRET_PATTERNS = [
    ("Flutterwave secret key", re.compile(rb"FLWSECK(?:_TEST)?-[A-Za-z0-9_-]{16,}")),
    ("Google OAuth client secret", re.compile(rb"GOCSPX-[A-Za-z0-9_-]{20,}")),
    ("Gmail OAuth refresh token", re.compile(rb"1//[A-Za-z0-9_-]{20,}")),
    ("private key material", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "credential-bearing database URL",
        re.compile(rb"postgres(?:ql)?(?:\+[A-Za-z0-9_]+)?://[^\s:/]+:[^\s@]+@", re.I),
    ),
    (
        "non-placeholder Gmail app password assignment",
        re.compile(
            rb"GMAIL_APP_PASSWORD\s*=\s*(?!(?:YOUR_|REPLACE|<|$))[A-Za-z0-9 _-]{12,}",
            re.I,
        ),
    ),
]


def _run_git(*args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        WARNINGS.append("git executable not found; repository secret checks were skipped.")
        return None


def _tracked_files() -> list[Path]:
    result = _run_git("ls-files", "-z")
    if result is None or result.returncode != 0:
        WARNINGS.append("Not inside a readable Git repository; tracked-file checks were skipped.")
        return []
    return [PROJECT_ROOT / item for item in result.stdout.split("\0") if item]


def _check_tracked_files() -> None:
    tracked = _tracked_files()
    tracked_relative = {path.relative_to(PROJECT_ROOT).as_posix() for path in tracked}

    if ".env" in tracked_relative or any(name.startswith(".env.") and not name.endswith(".example") for name in tracked_relative):
        FAILURES.append("A real .env file is tracked by Git. Secrets must never be committed.")

    tracked_databases = [
        name for name in tracked_relative
        if name.lower().endswith((".db", ".sqlite", ".sqlite3"))
    ]
    if tracked_databases:
        FAILURES.append("Customer/database files are tracked by Git: " + ", ".join(sorted(tracked_databases)))

    for path in tracked:
        try:
            if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(data):
                FAILURES.append(f"Possible {label} in tracked file: {path.relative_to(PROJECT_ROOT)}")


def _check_git_history() -> None:
    # Historical credentials still matter, but rotation is what neutralizes them.
    # Keep them visible as warnings so current code can pass CI once the live
    # credentials are rotated. The launch-readiness gate separately requires
    # LAUNCH_SECRETS_ROTATED=true before public live-money launch.
    result = _run_git("log", "-p", "--all", "--no-ext-diff", "--", ".")
    if result is None or result.returncode != 0:
        return
    history = result.stdout.encode("utf-8", errors="ignore")
    if re.search(rb"FLWSECK(?:_TEST)?-[A-Za-z0-9_-]{16,}", history):
        WARNINGS.append(
            "Git history contains an old Flutterwave secret key. Ensure it is revoked/rotated before launch."
        )
    if re.search(rb"GOCSPX-[A-Za-z0-9_-]{20,}", history):
        WARNINGS.append(
            "Git history contains an old Google OAuth client secret. Ensure it is revoked/rotated before launch."
        )
    if re.search(rb"1//[A-Za-z0-9_-]{20,}", history):
        WARNINGS.append(
            "Git history contains an old Gmail OAuth refresh token. Ensure it is revoked/regenerated before launch."
        )
    if re.search(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", history):
        WARNINGS.append(
            "Git history contains private-key material. Ensure the corresponding key was revoked/rotated."
        )


def _check_runtime_config() -> None:
    try:
        settings.validate_runtime()
    except RuntimeError as exc:
        FAILURES.append(str(exc))

    if settings.ENVIRONMENT != "production":
        if settings.DEBUG_RETURN_PAU_CODE:
            WARNINGS.append("DEBUG_RETURN_PAU_CODE is enabled. Keep it false except isolated local debugging.")
        if len(settings.SECRET_KEY) < 32 or "change" in settings.SECRET_KEY.lower():
            WARNINGS.append("Local SECRET_KEY is weak/placeholder. Generate a random one before serious testing.")
        if not settings.FLW_SECRET_HASH:
            WARNINGS.append("FLW_SECRET_HASH is missing; webhook calls will be rejected (safe, but webhooks will not work).")

    secret_test = settings.FLW_SECRET_KEY.upper().startswith("FLWSECK_TEST")
    public_test = settings.FLW_PUBLIC_KEY.upper().startswith("FLWPUBK_TEST")
    if settings.FLW_SECRET_KEY and settings.FLW_PUBLIC_KEY and secret_test != public_test:
        FAILURES.append("Flutterwave public/secret keys mix TEST and LIVE modes.")


def _check_gitignore() -> None:
    path = PROJECT_ROOT / ".gitignore"
    if not path.exists():
        FAILURES.append(".gitignore is missing.")
        return
    text = path.read_text(encoding="utf-8", errors="ignore")
    if ".env" not in text:
        FAILURES.append(".gitignore does not protect .env files.")
    if "*.db" not in text and "*.sqlite" not in text:
        FAILURES.append(".gitignore does not protect local database files.")


def main() -> None:
    _check_gitignore()
    _check_tracked_files()
    _check_git_history()
    _check_runtime_config()

    print("UniCart security preflight")
    print("==========================")
    if WARNINGS:
        print("\nWarnings:")
        for item in WARNINGS:
            print(f" - {item}")
    if FAILURES:
        print("\nBLOCKERS:")
        for item in FAILURES:
            print(f" - {item}")
        print("\nPreflight FAILED. Do not deploy production/live-money mode yet.")
        raise SystemExit(1)

    print("\nPreflight passed: no deployment blockers were detected by this local check.")


if __name__ == "__main__":
    main()
