from datetime import datetime
from ..extensions import db


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(250))
    subject = db.Column(db.String(250))
    date = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'))  # Добавляем внешний ключ для связи с таблицей User

    user = db.relationship('User', back_populates='posts')  # Связь с пользователем
