import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session as DBSession
from .models import Session, SessionLocal


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_session(db: DBSession, email: str) -> Session:
    sid = str(uuid.uuid4())
    session = Session(
        id=sid,
        user_email=email,
        incident_idx=0,
        phase_id="initial",
        decisions=[],
        started_at=_now(),
        updated_at=_now(),
        completed=False,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def get_session(db: DBSession, session_id: str) -> Session | None:
    return db.query(Session).filter(Session.id == session_id).first()


def update_session(db: DBSession, session: Session, **kwargs):
    for k, v in kwargs.items():
        setattr(session, k, v)
    session.updated_at = _now()
    db.commit()
    db.refresh(session)
    return session


def mark_completed(session_id: str):
    db = SessionLocal()
    try:
        session = get_session(db, session_id)
        if session:
            session.completed = True
            session.updated_at = _now()
            db.commit()
    finally:
        db.close()


def append_decision(db: DBSession, session: Session, decision: dict):
    decisions = list(session.decisions or [])
    decisions.append(decision)
    update_session(db, session, decisions=decisions)
