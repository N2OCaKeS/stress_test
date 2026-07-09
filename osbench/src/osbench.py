
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
progress.update_progress(10)
unixbench_test = UnixBench()
fsmark_test = FSMark()
lmbench_test = LMBench()
perf = PerfBench()
aggregator = BenchmarkAggregator()
index = IndexCalculator()
progress.update_progress(20)

# ==================== БЛОК UNIXBENCH ========================
progress.update_progress(30)
unixbench_test.start_test()
unixbench_test.get_results()
progress.update_progress(95)

# ==================== БЛОК FS_MARK ==========================
progress.advance_stage()
progress.update_progress(10)
fsmark_test.start_test()
fsmark_test.get_results()
progress.update_progress(95)

# ==================== БЛОК LMbench ==========================
progress.advance_stage()
progress.update_progress(10)
lmbench_test.start_test()
lmbench_test.get_results()
progress.update_progress(95)

# ==================== БЛОК Perf Bench ==========================
progress.advance_stage()
progress.update_progress(10)
perf.start_test()
perf.get_results()
progress.update_progress(95)

# ==================== БЛОК Results Aggregator ==========================
progress.advance_stage()
progress.update_progress(30)
aggregator.load_all()
aggregator.print_summary()
aggregator.export_to_json()
progress.update_progress(95)
# ==================== ЗАВЕРШЕНИЕ ========================
progress.stop()

# ==================== БЛОК Index Calculator ==========================
index.total_index_calculator(start_time=start_time)

