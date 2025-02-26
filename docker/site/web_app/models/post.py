from datetime import datetime, timezone
from ..extensions import db


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(250))
    subject = db.Column(db.String(250))
    date = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'))  # Добавляем внешний ключ для связи с таблицей User

    user = db.relationship('User', backref='user_posts', foreign_keys=[user_id])  # Связь с пользователем
