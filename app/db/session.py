import os
import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("db")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "ai_assistant_db")
DB_USER = os.getenv("DB_USER", "akbarov")
DB_PASSWORD = os.getenv("DB_PASSWORD", "akbarov")

DEFAULT_ASYNC_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
DEFAULT_SYNC_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_ASYNC_URL)
DATABASE_URL_SYNC = os.getenv("DATABASE_URL_SYNC", DEFAULT_SYNC_URL)

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

def get_sync_engine():
    sync_url = DATABASE_URL_SYNC
    if sync_url.startswith("postgresql+asyncpg://"):
        sync_url = sync_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    return create_engine(sync_url, pool_pre_ping=True)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def init_db():
    from app.db.models import Base
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("[DB] Database tables initialized successfully.")
    except Exception as exc:
        err_msg = str(exc).lower()
        if "already exists" in err_msg or "uniqueviolationerror" in err_msg or "duplicate key" in err_msg:
            logger.info("[DB] Database tables verified and ready.")
        else:
            logger.warning(f"[DB] Database initialization warning: {exc}")
