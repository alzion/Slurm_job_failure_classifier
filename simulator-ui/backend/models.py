from sqlalchemy import Column, String, Integer, Boolean, DateTime, JSON, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os

Base = declarative_base()


class Session(Base):
    __tablename__ = "simulator_sessions"
    id           = Column(String, primary_key=True)
    user_email   = Column(String, nullable=False)
    incident_idx = Column(Integer, default=0)
    phase_id     = Column(String, default="initial")
    decisions    = Column(JSON, default=list)
    started_at   = Column(DateTime)
    updated_at   = Column(DateTime)
    completed    = Column(Boolean, default=False)


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/simulator"
)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
