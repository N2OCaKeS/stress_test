from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
import redis
from .config import REDIS_HOST

redis_client = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
db = SQLAlchemy()
migrate = Migrate()
bcrypt = Bcrypt()
login_manager = LoginManager()
