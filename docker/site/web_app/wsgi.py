import sys
sys.path.insert(0, '/app')  # Добавляем путь к проекту в PYTHONPATH

from web_app import create_app

application = create_app()

if __name__ == "__main__":
    application.run()
