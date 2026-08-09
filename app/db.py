from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from .config import settings


class Base(DeclarativeBase):
    pass


def _fix_database_url(url: str) -> str:
    """
    Render (and some other hosts) provide a DATABASE_URL starting with
    'postgres://' but SQLAlchemy async requires 'postgresql+asyncpg://'.
    This fixes it automatically so deployment never fails on this.
    """
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


_database_url = _fix_database_url(settings.DATABASE_URL)

engine = create_async_engine(_database_url, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db():
    async with SessionLocal() as session:
        yield session