import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from app.api.v1.models.token import ActiveToken
from app.utils.config import settings

def create_active_token(
    db: Session,
    user_id: int,
    expires_delta: Optional[timedelta] = None
) -> ActiveToken:
    jti = str(uuid.uuid4())
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    token = ActiveToken(user_id=user_id, jti=jti, expires_at=expire)
    db.add(token)
    db.commit()
    db.refresh(token)
    return token

def get_active_token(db: Session, jti: str) -> Optional[ActiveToken]:
    return db.query(ActiveToken).filter(ActiveToken.jti == jti).first()

def list_active_tokens(db: Session, user_id: int) -> List[ActiveToken]:
    return db.query(ActiveToken).filter(ActiveToken.user_id == user_id).all()

def revoke_token(db: Session, jti: str) -> None:
    token = get_active_token(db, jti)
    if token:
        db.delete(token)
        db.commit()

def revoke_all_tokens(db: Session, user_id: int) -> None:
    db.query(ActiveToken).filter(ActiveToken.user_id == user_id).delete()
    db.commit()

def revoke_all_tokens_global(db: Session) -> None:
    db.query(ActiveToken).delete()
    db.commit()

def revoke_all_tokens_for_user(db: Session, user_id: int) -> int:
    """
    Удаляет все активные JWT-токены пользователя из таблицы tokens.
    Возвращает число удалённых записей.
    """
    deleted = (
        db.query(ActiveToken)
        .filter(ActiveToken.user_id == user_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return deleted