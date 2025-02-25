from datetime import datetime, timezone
from ..extensions import db


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"))
    subject = db.Column(db.String(250))
    date = db.Column(db.DateTime, default=datetime.now(timezone.utc))
    teacher_user = db.relationship("User", backref="teacher_posts", foreign_keys=[teacher])
