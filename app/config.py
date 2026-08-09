import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class Settings:
    def __init__(self) -> None:
        self.SECRET_KEY: str = os.getenv(
            "SECRET_KEY",
            "unicart_secret_key_change_this_in_production_min32chars",
        )
        self.ALGORITHM: str = os.getenv("ALGORITHM", "HS256")
        self.ACCESS_TOKEN_EXPIRE_MINUTES: int = int(
            os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60")
        )
        self.DATABASE_URL: str = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:dominoe8@127.0.0.1:5432/postgres",
        )
        self.ENTRY_FEE_NGN: int = int(os.getenv("ENTRY_FEE_NGN", "2000"))
        self.TARGET_ITEM_AMOUNT_NGN: int = int(
            os.getenv("TARGET_ITEM_AMOUNT_NGN", "50000")
        )

        # Flutterwave
        self.FLW_SECRET_KEY: str = os.getenv(
            "FLW_SECRET_KEY",
            "FLWSECK-972240d6fa943f0154ceebc6a48a5429-19e69a097f2vt-X",
        )
        self.FLW_PUBLIC_KEY: str = os.getenv(
            "FLW_PUBLIC_KEY",
            "FLWPUBK-2d489e876e5c76613cbf311ca564e033-X",
        )
        self.FLW_ENCRYPTION_KEY: str = os.getenv(
            "FLW_ENCRYPTION_KEY",
            "972240d6fa942aa577da74bd",
        )
        self.FLW_BASE_URL: str = os.getenv(
            "FLW_BASE_URL", "https://api.flutterwave.com/v3",
        )
        self.FLW_CALLBACK_URL: str = os.getenv(
            "FLW_CALLBACK_URL",
            "https://unicart-backend-v6u9.onrender.com/payments/callback",
        )

        self.ALLOWED_EMAIL_DOMAINS: str = os.getenv(
            "ALLOWED_EMAIL_DOMAINS", "pau.edu.ng",
        )
        self.PAU_CODE_EXPIRES_MINUTES: int = int(
            os.getenv("PAU_CODE_EXPIRES_MINUTES", "10")
        )
        self.DEBUG_RETURN_PAU_CODE: bool = (
            os.getenv("DEBUG_RETURN_PAU_CODE", "true").lower() == "true"
        )
        self.ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

        # Email
        self.ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "unicartbytekena@gmail.com")
        self.GMAIL_USER: str = os.getenv("GMAIL_USER", "")
        self.GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "")

        self.BACKEND_CORS_ORIGINS: list[str] = self._parse_origins(
            os.getenv(
                "BACKEND_CORS_ORIGINS",
                "https://unicartbytekena.onrender.com,"
                "https://epic-ice-fire.github.io,"
                "http://localhost:3000,http://localhost:8000,"
                "http://localhost:57318,http://127.0.0.1:8000",
            )
        )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @staticmethod
    def _parse_origins(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]


settings = Settings()