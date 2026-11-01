"""Database engine, sessions, and connectivity checks."""

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from smarthris.config import Settings, get_settings


def create_db_engine(settings: Settings | None = None, *, echo: bool = False) -> Engine:
    """Build a SQLAlchemy engine for MySQL."""
    cfg = settings or get_settings()
    return create_engine(
        cfg.sqlalchemy_database_url,
        echo=echo,
        pool_pre_ping=True,
        pool_recycle=3600,
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Session factory bound to the given engine."""
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def verify_database_connection(engine: Engine) -> bool:
    """
    Return True only after a real round-trip to the database (SELECT 1).
    Any connection or authentication error returns False.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


