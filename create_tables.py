import asyncio

from app.db import engine, Base

# VERY IMPORTANT: import all models so SQLAlchemy sees them
from app.models import (
    User,
    PauEmailVerification,
    Lobby,
    LobbyPass,
    LobbyItem,
)


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("✅ Tables created successfully.")


if __name__ == "__main__":
    asyncio.run(create_tables())