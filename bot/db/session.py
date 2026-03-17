from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import settings
from bot.db.models import Base

_db_url = settings.database_url

# Ensure the data directory exists for file-based SQLite.
if _db_url.startswith("sqlite"):
    _path_part = _db_url.split("///")[-1]
    if _path_part and _path_part != ":memory:":
        Path(_path_part).parent.mkdir(parents=True, exist_ok=True)

engine = create_async_engine(_db_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
