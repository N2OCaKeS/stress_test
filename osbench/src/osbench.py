
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from unixbench import UnixBench
from fs_mark import FsMark


"""
Назначение:
    Данный скрипт вызывается из главного модуля run.py при передаче аргумента --run.
    Он выполняет непосредственный запуск всех зарегистрированных тестов производительности.

Порядок работы:
    1. Инициализация объектов тестов 
    2. Последовательный запуск каждого теста
    3. Сбор и сохранение результатов каждого теста
"""


# ==================== БЛОК ИНИЦИАЛИЗАЦИИ ====================
ub_test = UnixBench()
fs_mark = FsMark()


# ==================== БЛОК UNIXBENCH ========================
ub_test.start_test()
ub_test.get_results()

# ==================== БЛОК FS_MARK ==========================
fs_mark.start_test()
fs_mark.get_results()

    
