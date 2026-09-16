from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # MVP compatibility migration for databases created by an earlier image.
    # Future schema changes must be promoted to Alembic migrations before release.
    columns = {column["name"] for column in inspect(engine).get_columns("upload_jobs")}
    if "created_at" not in columns:
        with engine.begin() as connection:
            if settings.database_url.startswith("sqlite"):
                connection.execute(text("ALTER TABLE upload_jobs ADD COLUMN created_at DATETIME"))
            else:
                connection.execute(text("ALTER TABLE upload_jobs ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP"))
