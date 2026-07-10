
import sys

from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from unixbench import UnixBench
from fs_mark import FSMark
from lmbench import LMBench
from perfbench import PerfBench
from aggregator import BenchmarkAggregator
from index_calc import IndexCalculator
from lib import ProgressBar


start_time = datetime.now()
progress = ProgressBar()
progress.start()

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
index = IndexCalculator()

# ==================== БЛОК UNIXBENCH ========================
unixbench_test.start_test()
unixbench_test.get_results()
progress.advance_stage()

# ==================== БЛОК FS_MARK ==========================
fsmark_test.start_test()
fsmark_test.get_results()
progress.advance_stage()

# ==================== БЛОК LMbench ==========================
lmbench_test.start_test()
lmbench_test.get_results()
progress.advance_stage()

# ==================== БЛОК Perf Bench ==========================
perf.start_test()
perf.get_results()
progress.advance_stage()

# ==================== БЛОК Results Aggregator ==========================
aggregator.load_all()
aggregator.print_summary()
aggregator.export_to_json()
progress.advance_stage()
# ==================== ЗАВЕРШЕНИЕ ========================
progress.stop()

# ==================== БЛОК Index Calculator ==========================
index.total_index_calculator(start_time=start_time)

