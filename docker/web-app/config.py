import os

class Config(object):
    USER = os.environ.get("POSTGRES_USER", "u")
    PASSWORD = os.environ.get("POSTGRES_PASSWORD", "1")
    HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    PORT = os.environ.get("POSTGRES_PORT", "5432")
    DB = os.environ.get("POSTGRES_DB", "mydb")

    SQLALCHEMY_DATABASE_URI = f"postgresql://{USER}:{PASSWORD}@{HOST}:{PORT}/{DB}"
    SECRET_KEY = "gjij4it3gj3094805gj83rg"
    SQLALCHEMY_TRACK_MODIFICATIONS = True