import os
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


_DEFAULT_DEV_SECRET = "unicart_secret_key_change_this_in_production_min32chars"


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development").strip().lower()

        # Authentication / JWT
        self.SECRET_KEY: str = os.getenv("SECRET_KEY", _DEFAULT_DEV_SECRET)
        self.ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
        self.ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
            os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
        )
        self.REMEMBERED_SESSION_DAYS: int = int(
            os.getenv("REMEMBERED_SESSION_DAYS", "30")
        )
        self.JWT_ISSUER: str = os.getenv("JWT_ISSUER", "unicart-api")
        self.JWT_AUDIENCE: str = os.getenv("JWT_AUDIENCE", "unicart-app")
        self.MAX_ACTIVE_SESSIONS_PER_USER: int = int(
            os.getenv("MAX_ACTIVE_SESSIONS_PER_USER", "5")
        )
        self.SESSION_RETENTION_DAYS: int = int(
            os.getenv("SESSION_RETENTION_DAYS", "30")
        )

        # Database
        self.DATABASE_URL: str = os.getenv(
            "DATABASE_URL",
            "sqlite+aiosqlite:///./unicart.db",
        )
        self.DB_CONNECT_TIMEOUT_SECONDS: int = int(
            os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "10")
        )
        self.AUTO_CREATE_TABLES: bool = _env_bool("AUTO_CREATE_TABLES", False)
        self.MAX_REQUEST_BODY_BYTES: int = int(
            os.getenv("MAX_REQUEST_BODY_BYTES", "1048576")
        )
        self.MAX_WEBHOOK_BODY_BYTES: int = int(
            os.getenv("MAX_WEBHOOK_BODY_BYTES", "65536")
        )
        self.MAX_AUDIT_DETAILS_BYTES: int = int(
            os.getenv("MAX_AUDIT_DETAILS_BYTES", "4096")
        )
        self.PAYMENT_CHECKOUT_TTL_MINUTES: int = int(
            os.getenv("PAYMENT_CHECKOUT_TTL_MINUTES", "30")
        )

        # Business configuration
        self.ENTRY_FEE_NGN: int = int(os.getenv("ENTRY_FEE_NGN", "2000"))
        self.TARGET_ITEM_AMOUNT_NGN: int = int(
            os.getenv("TARGET_ITEM_AMOUNT_NGN", "50000")
        )

        # Flutterwave. Never provide source-code fallback credentials.
        self.FLW_SECRET_KEY: str = os.getenv("FLW_SECRET_KEY", "").strip()
        self.FLW_PUBLIC_KEY: str = os.getenv("FLW_PUBLIC_KEY", "").strip()
        self.FLW_ENCRYPTION_KEY: str = os.getenv("FLW_ENCRYPTION_KEY", "").strip()
        self.FLW_SECRET_HASH: str = os.getenv("FLW_SECRET_HASH", "").strip()
        self.FLW_BASE_URL: str = os.getenv(
            "FLW_BASE_URL", "https://api.flutterwave.com/v3",
        ).rstrip("/")
        self.FLW_CALLBACK_URL: str = os.getenv(
            "FLW_CALLBACK_URL",
            "http://127.0.0.1:8000/payments/callback",
        ).strip()

        # PAU verification
        self.ALLOWED_EMAIL_DOMAINS: str = os.getenv(
            "ALLOWED_EMAIL_DOMAINS", "pau.edu.ng",
        )
        self.PAU_CODE_EXPIRES_MINUTES: int = int(
            os.getenv("PAU_CODE_EXPIRES_MINUTES", "10")
        )
        self.DEBUG_RETURN_PAU_CODE: bool = _env_bool("DEBUG_RETURN_PAU_CODE", False)

        # Email. Production prefers Gmail API over HTTPS because Render cannot
        # reach Gmail SMTP reliably. SMTP remains a local-development fallback.
        self.ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "unicartbytekena@gmail.com")
        self.GMAIL_USER: str = os.getenv("GMAIL_USER", "").strip()
        self.GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "").strip()
        self.GMAIL_API_CLIENT_ID: str = os.getenv("GMAIL_API_CLIENT_ID", "").strip()
        self.GMAIL_API_CLIENT_SECRET: str = os.getenv("GMAIL_API_CLIENT_SECRET", "").strip()
        self.GMAIL_API_REFRESH_TOKEN: str = os.getenv("GMAIL_API_REFRESH_TOKEN", "").strip()

        # CORS
        self.BACKEND_CORS_ORIGINS: list[str] = self._parse_origins(
            os.getenv(
                "BACKEND_CORS_ORIGINS",
                "http://localhost:3000,http://localhost:8000,"
                "http://127.0.0.1:3000,http://127.0.0.1:8000",
            )
        )
        self.ALLOWED_HOSTS: list[str] = self._parse_hosts(
            os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
        )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @staticmethod
    def _parse_origins(value: str) -> list[str]:
        return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]

    @staticmethod
    def _parse_hosts(value: str) -> list[str]:
        return [item.strip().lower() for item in value.split(",") if item.strip()]

    def validate_runtime(self) -> None:
        """Fail closed when a production deployment is dangerously configured."""
        errors: list[str] = []

        if self.is_production:
            if self.SECRET_KEY == _DEFAULT_DEV_SECRET or len(self.SECRET_KEY) < 32:
                errors.append("SECRET_KEY must be a unique random value of at least 32 characters.")
            if self.DEBUG_RETURN_PAU_CODE:
                errors.append("DEBUG_RETURN_PAU_CODE must be false in production.")
            if self.AUTO_CREATE_TABLES:
                errors.append("AUTO_CREATE_TABLES must be false in production; use migrations.")
            if self.DATABASE_URL.startswith("sqlite"):
                errors.append("Production DATABASE_URL must not use local SQLite.")
            if not self.FLW_SECRET_KEY:
                errors.append("FLW_SECRET_KEY is required in production.")
            if not self.FLW_SECRET_HASH:
                errors.append("FLW_SECRET_HASH is required so Flutterwave webhooks can be authenticated.")

            gmail_api_values = (
                self.GMAIL_API_CLIENT_ID,
                self.GMAIL_API_CLIENT_SECRET,
                self.GMAIL_API_REFRESH_TOKEN,
            )
            gmail_api_ready = all(gmail_api_values)
            gmail_api_partial = any(gmail_api_values) and not gmail_api_ready
            if gmail_api_partial:
                errors.append(
                    "Gmail API OAuth configuration is incomplete; set "
                    "GMAIL_API_CLIENT_ID, GMAIL_API_CLIENT_SECRET and "
                    "GMAIL_API_REFRESH_TOKEN together."
                )
            if not self.GMAIL_USER or not (gmail_api_ready or self.GMAIL_APP_PASSWORD):
                errors.append(
                    "Email delivery requires GMAIL_USER plus either complete Gmail API OAuth "
                    "credentials or GMAIL_APP_PASSWORD."
                )

            if "*" in self.BACKEND_CORS_ORIGINS:
                errors.append("Wildcard CORS origins are forbidden in production.")
            if not self.ALLOWED_HOSTS or "*" in self.ALLOWED_HOSTS:
                errors.append("ALLOWED_HOSTS must explicitly list production API hostnames.")
            for origin in self.BACKEND_CORS_ORIGINS:
                parsed_origin = urlparse(origin)
                if parsed_origin.scheme != "https" or not parsed_origin.netloc:
                    errors.append(
                        f"Production CORS origin must be HTTPS: {origin!r}."
                    )
                if parsed_origin.hostname in {"localhost", "127.0.0.1", "::1"}:
                    errors.append("Localhost CORS origins are forbidden in production.")
            if self.FLW_SECRET_KEY.upper().startswith("FLWSECK_TEST"):
                errors.append("Production must use a Flutterwave LIVE secret key, not a TEST key.")
            if self.FLW_PUBLIC_KEY.upper().startswith("FLWPUBK_TEST"):
                errors.append("Production must use a Flutterwave LIVE public key, not a TEST key.")

            callback = urlparse(self.FLW_CALLBACK_URL)
            if callback.scheme != "https" or not callback.netloc:
                errors.append("FLW_CALLBACK_URL must be a public HTTPS URL in production.")

        if self.ALGORITHM != "HS256":
            errors.append("UniCart currently supports HS256 JWT signing only.")
        if self.ACCESS_TOKEN_EXPIRE_MINUTES <= 0 or self.ACCESS_TOKEN_EXPIRE_MINUTES > 1440:
            errors.append("ACCESS_TOKEN_EXPIRE_MINUTES must be between 1 and 1440.")
        if self.REMEMBERED_SESSION_DAYS < 1 or self.REMEMBERED_SESSION_DAYS > 90:
            errors.append("REMEMBERED_SESSION_DAYS must be between 1 and 90.")
        if self.MAX_ACTIVE_SESSIONS_PER_USER < 1 or self.MAX_ACTIVE_SESSIONS_PER_USER > 20:
            errors.append("MAX_ACTIVE_SESSIONS_PER_USER must be between 1 and 20.")
        if self.SESSION_RETENTION_DAYS < 1 or self.SESSION_RETENTION_DAYS > 365:
            errors.append("SESSION_RETENTION_DAYS must be between 1 and 365.")
        if self.PAU_CODE_EXPIRES_MINUTES <= 0 or self.PAU_CODE_EXPIRES_MINUTES > 30:
            errors.append("PAU_CODE_EXPIRES_MINUTES must be between 1 and 30.")
        if self.ENTRY_FEE_NGN <= 0 or self.TARGET_ITEM_AMOUNT_NGN <= 0:
            errors.append("Payment amounts must be positive.")
        flw_base = urlparse(self.FLW_BASE_URL)
        if flw_base.scheme != "https" or not flw_base.netloc:
            errors.append("FLW_BASE_URL must be a valid HTTPS URL.")
        if self.is_production and flw_base.hostname != "api.flutterwave.com":
            errors.append("Production FLW_BASE_URL must point to api.flutterwave.com.")
        if self.MAX_REQUEST_BODY_BYTES < 1024 or self.MAX_REQUEST_BODY_BYTES > 10 * 1024 * 1024:
            errors.append("MAX_REQUEST_BODY_BYTES must be between 1 KiB and 10 MiB.")
        if self.MAX_WEBHOOK_BODY_BYTES < 1024 or self.MAX_WEBHOOK_BODY_BYTES > self.MAX_REQUEST_BODY_BYTES:
            errors.append("MAX_WEBHOOK_BODY_BYTES must be between 1 KiB and MAX_REQUEST_BODY_BYTES.")
        if self.MAX_AUDIT_DETAILS_BYTES < 512 or self.MAX_AUDIT_DETAILS_BYTES > 65536:
            errors.append("MAX_AUDIT_DETAILS_BYTES must be between 512 and 65536.")
        if self.PAYMENT_CHECKOUT_TTL_MINUTES < 5 or self.PAYMENT_CHECKOUT_TTL_MINUTES > 180:
            errors.append("PAYMENT_CHECKOUT_TTL_MINUTES must be between 5 and 180.")

        if errors:
            raise RuntimeError("Unsafe UniCart configuration:\n- " + "\n- ".join(errors))


settings = Settings()
