from flask import Flask

from .config import Config

from .routes.home import home_bp


def create_app(config_class=Config):
    app = Flask(__name__, static_folder="static")
    app.config.from_object(config_class)

    app.register_blueprint(home_bp)

    # with app.app_context():
    #     db.create_all()

    return app


