import sys
sys.path.insert(0, '/app')  # Добавляем путь к проекту в PYTHONPATH

from web_app import create_app

app = create_app()

if __name__ == "__main__":
    app.run()
