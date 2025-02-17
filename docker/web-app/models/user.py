from ..extensions import db
from datetime import datetime, timezone

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(50), default='user')
    name = db.Column(db.String(50))
    login = db.Column(db.String(20))
    password = db.Column(db.String(200))
    date = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    avatar = db.Column(db.String(200))