
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from unixbench import UnixBench
from fs_mark import FSMark
from lmbench import LMBench
from perfbench import PerfBench
from aggregator import BenchmarkAggregator


"""
Назначение:
    Скрипт выполняет непосредственный запуск всех зарегистрированных тестов производительности.
    Вызывается из главного модуля run.py.

Порядок работы:
    1. Инициализация объектов тестов 
    2. Последовательный запуск каждого теста
    3. Сбор и сохранение результатов каждого теста
    4. Аггрегация результатов на подсистемы
"""


# ==================== БЛОК ИНИЦИАЛИЗАЦИИ ====================
unixbench_test = UnixBench()
fsmark_test = FSMark()
lmbench_test = LMBench()
perf = PerfBench()
aggregator = BenchmarkAggregator()


# ==================== БЛОК UNIXBENCH ========================
unixbench_test.start_test()
unixbench_test.get_results()

# ==================== БЛОК FS_MARK ==========================
fsmark_test.start_test()
fsmark_test.get_results()

# ==================== БЛОК LMbench ==========================
lmbench_test.start_test()
lmbench_test.get_results()

# ==================== БЛОК Perf Bench ==========================
perf.start_test()
perf.get_results()

# ==================== БЛОК Results Aggregator ==========================
aggregator.load_all()
aggregator.print_summary()
aggregator.export_to_json()

