import os

class Config(object):
    APPNAME = 'web_app'
    ROOT = os.path.abspath(APPNAME)
    WEB_UPLOAD = '/static/upload'
    SERVER_PATH = os.path.join(ROOT, WEB_UPLOAD)

    USER = os.environ.get("POSTGRES_USER", "u")
    PASSWORD = os.environ.get("POSTGRES_PASSWORD", "1")
    HOST = os.environ.get("PGBOUNCER_HOST", "127.0.0.1")
    PORT = os.environ.get("PGBOUNCER_PORT", "6432")
    DB = os.environ.get("POSTGRES_DB", "mydb")

    SQLALCHEMY_DATABASE_URI = f"postgresql://{USER}:{PASSWORD}@{HOST}:{PORT}/{DB}"
    SECRET_KEY = os.environ.get("SECRET_KEY", "gjij4it3gj3094805gj83rg")

    # Улучшенные настройки пула соединений
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_size": 60,          # Количество постоянных соединений
        "max_overflow": 40,       # Дополнительные соединения (при пиковых нагрузках)
        "pool_recycle": 300,     # Перезапуск соединения каждые 30 минут (защита от отключений)
        "pool_timeout": 15,       # Максимальное время ожидания свободного соединения
        "pool_pre_ping": True     # Проверка активности соединения перед использованием
    }

    SQLALCHEMY_TRACK_MODIFICATIONS = False


REDIS_HOST = "127.0.0.1"
