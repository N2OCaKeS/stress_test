import sys
import os
sys.path.insert(0, "/app")

from web_app import create_app

application = create_app()

if __name__ == "__main__":
    application.run()
