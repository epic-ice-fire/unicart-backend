import ssl

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


def _fix_database_url(url: str) -> str:
    """Normalize common PostgreSQL URLs for SQLAlchemy's asyncpg driver."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _build_engine():
    database_url = _fix_database_url(settings.DATABASE_URL)
    url: URL = make_url(database_url)

    options: dict = {
        "echo": False,
        "pool_pre_ping": True,
    }

    if url.drivername == "postgresql+asyncpg":
        # Hosted Postgres providers often append ?ssl=require or
        # ?sslmode=require.  Convert that query parameter into an explicit
        # SSLContext so asyncpg gets a consistent value on Windows/Linux.
        ssl_mode = url.query.get("ssl") or url.query.get("sslmode")
        if ssl_mode is not None:
            url = url.difference_update_query(["ssl", "sslmode"])
            mode = str(ssl_mode).lower()
            if mode not in {"disable", "false", "0"}:
                try:
                    import certifi

                    ssl_context = ssl.create_default_context(cafile=certifi.where())
                except Exception:
                    ssl_context = ssl.create_default_context()
                options["connect_args"] = {
                    "ssl": ssl_context,
                    "timeout": settings.DB_CONNECT_TIMEOUT_SECONDS,
                }
            else:
                options["connect_args"] = {
                    "ssl": False,
                    "timeout": settings.DB_CONNECT_TIMEOUT_SECONDS,
                }
        else:
            options["connect_args"] = {
                "timeout": settings.DB_CONNECT_TIMEOUT_SECONDS,
            }

        # Hosted databases can close idle connections. These settings prevent
        # stale connections from being reused after the app has been idle.
        options["pool_recycle"] = 300

    return create_async_engine(url, **options)


engine = _build_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db():
    async with SessionLocal() as session:
        yield session
