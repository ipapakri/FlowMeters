"""
SQLAlchemy models and session factory.
All scraped MQTT data is cached here so API routes don't hit the broker on every request.
"""

import os
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./flowmeters.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class RealtimeDevice(Base):
    """Latest realtime snapshot for each device (upserted every poll interval)."""

    __tablename__ = "realtime_devices"

    device_id = Column(Integer, primary_key=True)
    device_name = Column(String, nullable=True)
    serial_no = Column(String, nullable=True)
    product_name = Column(String, nullable=True)
    group_name = Column(String, nullable=True)
    # JSON-encoded list of {paramName, paramValue, paramUnit}
    params_json = Column(Text, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class DashboardDataPoint(Base):
    """
    Time-series snapshots from wm/device/dashboard/data/list.
    Each poll appends rows; old rows are pruned after DASHBOARD_RETENTION_DAYS.
    """

    __tablename__ = "dashboard_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(Integer, nullable=True)
    device_name = Column(String, nullable=True)
    serial_no = Column(String, nullable=True)
    param_name = Column(String, nullable=True)
    flow = Column(Float, nullable=True)
    instantaneous_flow = Column(Float, nullable=True)
    instantaneous_velocity = Column(Float, nullable=True)
    water_temperature = Column(Float, nullable=True)
    accumulated_cooling = Column(Float, nullable=True)
    heat = Column(Float, nullable=True)
    issue_date = Column(String, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class CumulativeDataPoint(Base):
    """
    Historical cumulative readings from wm/device/cumulative/data/list.
    One row per (device_id, issue_date, param_name) — upserted on every fetch.
    """

    __tablename__ = "cumulative_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(Integer, nullable=False, index=True)
    device_name = Column(String, nullable=True)
    serial_no = Column(String, nullable=True)
    param_name = Column(String, nullable=True)
    issue_date = Column(String, nullable=True)
    flow = Column(Float, nullable=True)
    instantaneous_flow = Column(Float, nullable=True)
    instantaneous_velocity = Column(Float, nullable=True)
    water_temperature = Column(Float, nullable=True)
    return_water_temperature = Column(Float, nullable=True)
    accumulated_cooling = Column(Float, nullable=True)
    heat = Column(Float, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)


def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency — yields a DB session and closes it after the request."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
