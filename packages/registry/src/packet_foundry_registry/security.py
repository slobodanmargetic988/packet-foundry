"""Opaque factory credentials carry precisely one factory identity."""

from __future__ import annotations

import hashlib
import secrets

from packet_foundry_contracts import EnrollmentCreate, utcnow
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .database import Factory


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_factory_token() -> str:
    return "pf_factory_" + secrets.token_urlsafe(32)


def enroll(session: Session, request: EnrollmentCreate) -> str:
    token = new_factory_token()
    session.add(Factory(
        factory_id=request.factory_id, name=request.name, token_digest=token_digest(token),
        created_at=utcnow(), revoked=False, draining=False,
    ))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ValueError("Factory already enrolled") from None
    return token
