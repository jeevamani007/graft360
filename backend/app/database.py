from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


def _build_url():
    if config.DATABASE_URL_OVERRIDE:
        return config.DATABASE_URL_OVERRIDE
    return URL.create(
        "postgresql+psycopg2",
        username=config.DB_USER,
        password=config.DB_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_PORT,
        database=config.DB_NAME,
    )


def ensure_database():
    """Create the PostgreSQL database 'TASK-PROJECT' if it does not exist yet."""
    if config.DATABASE_URL_OVERRIDE:
        return
    import psycopg2
    from psycopg2 import sql

    conn = psycopg2.connect(
        dbname="postgres", user=config.DB_USER, password=config.DB_PASSWORD,
        host=config.DB_HOST, port=config.DB_PORT,
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (config.DB_NAME,))
            if cur.fetchone() is None:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(config.DB_NAME)))
    finally:
        conn.close()


ensure_database()
engine = create_engine(_build_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
