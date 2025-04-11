import os
import sys
sys.path.insert(0, os.path.abspath('../allta'))  # Добавляем путь к коду проекта

# Основные настройки проекта
project = 'Allta'
author = 'team13'
release = '1.0.0'

# Расширения Sphinx
extensions = [
    'sphinx.ext.autodoc',     # Автоматическая генерация документации из docstring'ов
    'sphinx.ext.napoleon',    # Поддержка стиля Google и NumPy для docstring'ов
    'sphinx.ext.viewcode'     # Добавление ссылок на исходный код в документации
]

# Путь к шаблонам
templates_path = ['_templates']


# Список файлов и папок, которые следует исключить из сборки
exclude_patterns = []

# Настройка темы оформления
# html_theme = 'sphinx_pdj_theme'
html_theme_options = {}

# Пути для статических файлов (CSS, изображения и т.д.)
html_static_path = ['_static']
html_permalinks = False