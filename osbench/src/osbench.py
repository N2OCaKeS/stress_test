
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from unixbench import UnixBench
from fs_mark import FsMark


"""
Назначение:
    Скрипт выполняет непосредственный запуск всех зарегистрированных тестов производительности.
    Вызывается из главного модуля run.py.

Порядок работы:
    1. Инициализация объектов тестов 
    2. Последовательный запуск каждого теста
    3. Сбор и сохранение результатов каждого теста
"""


# ==================== БЛОК ИНИЦИАЛИЗАЦИИ ====================
unixbench_test = UnixBench()
fsmark_test = FsMark()


# ==================== БЛОК UNIXBENCH ========================
unixbench_test.start_test()
unixbench_test.get_results()

# ==================== БЛОК FS_MARK ==========================
fsmark_test.start_test()
fsmark_test.get_results()

    
