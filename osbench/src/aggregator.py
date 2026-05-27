import json
import pandas as pd

from os import path, makedirs
from osb_logger import log

from config.conf import RESULTS_MAIN_DIR, SUBSYSTEM_RESULTS



class BenchmarkAggregator:
    """
    Агрегатор результатов всех бенчмарков
    """
    
    # Маппинг тестов на подсистемы
    SUBSYSTEM_MAPPING = {
        # Ядро и системные вызовы
        'kernel': {
            'unixbench': ['syscall'],
            'lmbench': ['lat_syscall', 'lat_ctx', 'lat_sig'],
            'perf': ['sched', 'epoll', 'futex']  # sched pipe, sched messaging, epoll wait, epoll ctl, futex hash, futex wake, futex requeue
        },
        # Процессы и IPC
        'processes_ipc': {
            'unixbench': ['execl', 'pipe', 'context1', 'spawn'],
            'lmbench': ['lat_pipe', 'bw_pipe', 'lat_proc'],
            'perf': []  
        },
        # Файловая система
        'filesystem': {
            'unixbench': ['fstime', 'fsbuffer', 'fsdisk'],
            'lmbench': ['lat_fs', 'bw_file_rd'],
            'fs_mark': ['speed', 'create_avg', 'write_avg', 'fsync_avg']
        },
        # Скрипты
        'scripts': {
            'unixbench': ['shell1', 'shell8'],
            'lmbench': [],
            'perf': []
        }
    }
    
    def __init__(self, 
                 results_dir=RESULTS_MAIN_DIR):
        """
        Инициализация агрегатора
        
        Args:
            results_dir: директория с JSON файлами результатов
        """
        self.results_dir = results_dir
        self.data = {
            'unixbench': None,
            'lmbench': None,
            'perf_bench': None,
            'fs_mark': None
        }
        
    def load_all(self):
        """Загружает все JSON файлы"""
        files = {
            'unixbench': 'unixbench_results.json',
            'lmbench': 'lmbench_results.json',
            'perf_bench': 'perf_bench_results.json',
            'fs_mark': 'fs_mark_results.json'
        }
        
        for key, filename in files.items():
            filepath = path.join(self.results_dir, filename)
            if path.exists(filepath):
                with open(filepath, 'r') as f:
                    self.data[key] = json.load(f)
                log.info(f"Загружен {filename}")
            else:
                log.warning(f"Файл не найден: {filename}")
    
        
    def extract_unixbench_results(self):
        """
        Извлекает результаты UnixBench для всех copies
        """
        if self.data['unixbench'] is None:
            return {}
        
        # Возвращаем все результаты для всех copies
        all_results = {}
        for copies, ub_data in self.data['unixbench'].items():
            tests = ub_data.get('tests', {})
            all_results[copies] = {}
            for test_name, test_data in tests.items():
                all_results[copies][test_name] = {
                    'value': test_data['value'],
                    'unit': test_data['measure']
                }
        return all_results
    
    def extract_lmbench_results(self):
        """
        Извлекает результаты LMbench 
        """
        if self.data['lmbench'] is None:
            return {}
        
        return self.data['lmbench']
    
    def extract_perf_results(self):
        """Извлекает результаты perf bench"""
        if self.data['perf_bench'] is None:
            return {}
        return self.data['perf_bench']
    
    def extract_fs_mark_results(self):
        """
        Извлекает результаты fs_mark с группировкой по количеству файлов
        """
        if self.data['fs_mark'] is None:
            return {}
        
        fs_data = self.data['fs_mark']
        results = {}
        
        metric_keys = ['speed', 'app_overhead', 'create_avg', 'write_avg', 
                       'fsync_avg', 'sync_avg', 'close_avg', 'unlink_avg']
        
        # Собираем все уникальные значения количества файлов
        all_f_counts = set()
        for key in metric_keys:
            if key in fs_data and isinstance(fs_data[key], dict):
                all_f_counts.update(fs_data[key].keys())
        
        # Группируем результаты по количеству файлов
        for f_count in sorted(all_f_counts, key=int): 
            group_key = f"{f_count}_files"
            results[group_key] = {}
            
            for key in metric_keys:
                if key in fs_data:
                    if isinstance(fs_data[key], dict) and f_count in fs_data[key]:
                        results[group_key][key] = {
                            'value': fs_data[key][f_count],
                            'unit': 'ops/sec' if key == 'speed' else 'microseconds'
                        }
                    elif not isinstance(fs_data[key], dict):
                        results[group_key][key] = {
                            'value': fs_data[key],
                            'unit': 'ops/sec' if key == 'speed' else 'microseconds'
                        }
        
        return results
    
    def build_subsystem_table(self):
        """
        Строит таблицу результатов по подсистемам 
        """
        ub_results = self.extract_unixbench_results()
        lm_results = self.extract_lmbench_results()
        perf_results = self.extract_perf_results()
        fs_results = self.extract_fs_mark_results()
        
        table_data = []
        
        for subsystem, tests in self.SUBSYSTEM_MAPPING.items():
            subsystem_results = {}
            
            # UnixBench тесты 
            for test_name in tests.get('unixbench', []):
                subsystem_results[test_name] = {}
                for copies, ub_data in ub_results.items():
                    if test_name in ub_data:
                        subsystem_results[test_name][copies] = ub_data[test_name]
            
            # LMbench тесты
            for test_name, test_data in lm_results.items():
                test_subsystem = self._get_subsystem_for_test('lmbench', test_name)
                if test_subsystem == subsystem:
                    subsystem_results[test_name] = test_data
            
            # Perf тесты
            for test_name, test_data in perf_results.items():
                test_subsystem = self._get_subsystem_for_test('perf bench', test_name)
                if test_subsystem == subsystem:
                    subsystem_results[test_name] = {
                        'value': test_data['value'],
                        'unit': test_data['unit']
                    }
            
            # fs_mark тесты
            for test_name, test_data in fs_results.items():
                subsystem_results[test_name] = test_data
            
            table_data.append({
                'subsystem': subsystem,
                'tests_count': len(subsystem_results),
                'results': subsystem_results
            })
        
        return table_data
    
    def create_dataframe(self):
        """Создает DataFrame со всеми результатами"""
        ub_results = self.extract_unixbench_results()
        lm_results = self.extract_lmbench_results()
        perf_results = self.extract_perf_results()
        fs_results = self.extract_fs_mark_results()
        
        rows = []
        
        # UnixBench результаты 
        for copies, ub_data in ub_results.items():
            for test_name, test_data in ub_data.items():
                rows.append({
                    'source': 'UnixBench',
                    'test_name': f"{test_name} ({copies} copies)",
                    'subsystem': self._get_subsystem_for_test('unixbench', test_name),
                    'value': test_data['value'],
                    'unit': test_data['unit']
                })
        
        # LMbench, Perf, fs_mark 
        for test_name, test_data in lm_results.items():
            rows.append({
                'source': 'LMbench',
                'test_name': test_name,
                'subsystem': self._get_subsystem_for_test('lmbench', test_name),
                'value': test_data['value'],
                'unit': test_data['unit']
            })
        
        for test_name, test_data in perf_results.items():
            rows.append({
                'source': 'perf bench',
                'test_name': test_name,
                'subsystem': self._get_subsystem_for_test('perf bench', test_name),
                'value': test_data['value'],
                'unit': test_data['unit']
            })
        
        for test_name, test_data in fs_results.items():
            rows.append({
                'source': 'fs_mark',
                'test_name': f'fs_mark_{test_name}',
                'subsystem': self._get_subsystem_for_test('fs_mark', test_name),
                'value': test_data['value'],
                'unit': test_data['unit']
            })
        
        return pd.DataFrame(rows)
    
    def _get_subsystem_for_test(self, source, test_name):
        """Определяет подсистему для теста"""
        for subsystem, tests in self.SUBSYSTEM_MAPPING.items():
            if source == 'unixbench' and test_name in tests.get('unixbench', []):
                return subsystem
            elif source == 'lmbench':
                for lm_test in tests.get('lmbench', []):
                    if lm_test in test_name:
                        return subsystem
            elif source == 'perf bench':
                for perf_test in tests.get('perf', []):
                    if perf_test in test_name:  
                        return subsystem
            elif source == 'fs_mark' and test_name in tests.get('fs_mark', []):
                return subsystem
        return 'other'
    
    def print_summary(self):
        """Выводит сводку результатов по подсистемам"""
        table_data = self.build_subsystem_table()
        
        log.info("\n" + "="*80)
        log.info("СВОДКА РЕЗУЛЬТАТОВ ПО ПОДСИСТЕМАМ")
        log.info("="*80)
        
        for subsystem_info in table_data:
            subsystem = subsystem_info['subsystem']
            results = subsystem_info['results']
            
            log.info(f"\n{'='*80}")
            log.info(f"ПОДСИСТЕМА: {subsystem.upper()}")
            log.info(f"{'='*80}")
            
            if not results:
                log.info("  Нет данных")
                continue
            
            for test_name, test_data in results.items():
                # Проверяем, UnixBench это или другой тест
                if test_name in self.SUBSYSTEM_MAPPING[subsystem].get('unixbench', []):
                    # UnixBench - выводим для каждого количества копий
                    log.info(f"\n  {test_name}:")
                    for copies, copies_data in test_data.items():
                        value = copies_data['value']
                        unit = copies_data['unit']
                        if value > 1000:
                            log.info(f"    {copies} копий: {value:>15,.2f} {unit}")
                        else:
                            log.info(f"    {copies} копий: {value:>15.4f} {unit}")
                else:
                    # Другие тесты
                    value = test_data['value']
                    unit = test_data['unit']
                    if value > 1000:
                        log.info(f"  {test_name:40}: {value:>15,.2f} {unit}")
                    else:
                        log.info(f"  {test_name:40}: {value:>15.4f} {unit}")
        
        # Создаем DataFrame для анализа
        df = self.create_dataframe()
        log.debug("\nDataFrame со всеми результатами:")
        log.debug(df.to_string())
    
    def export_to_json(self, output_path=SUBSYSTEM_RESULTS):
        """Экспортирует результаты в JSON"""
        table_data = self.build_subsystem_table()
        
        output_data = {}
        for subsystem_info in table_data:
            subsystem = subsystem_info['subsystem']
            results = subsystem_info['results']
            
            output_data[subsystem] = {}
            for test_name, test_data in results.items():
                # Сохраняем как есть (с copies для UnixBench)
                output_data[subsystem][test_name] = test_data
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)
        
        log.info(f"Результаты экспортированы в {output_path}")

        