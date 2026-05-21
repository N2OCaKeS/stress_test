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
            'perf': ['sched', 'epoll']  # sched pipe, sched messaging, epoll wait, epoll ctl
        },
        # Процессы и IPC
        'processes_ipc': {
            'unixbench': ['execl', 'pipe', 'context1', 'spawn'],
            'lmbench': ['lat_pipe', 'bw_pipe', 'lat_proc'],
            'perf': ['futex']  # futex hash, futex wake, futex requeue
        },
        # Файловая система
        'filesystem': {
            'unixbench': ['fstime', 'fsbuffer', 'fsdisk'],
            'lmbench': ['lat_fs', 'bw_file_rd'],
            'fs_mark': ['speed', 'create_avg', 'write_avg', 'fsync_avg']
        },
        # Пользовательская нагрузка
        'user': {
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
    
    def _clean_lmbench_name(self, test_name):
        """
        Очищает имя LMbench теста от лишних параметров
        
        Args:
            test_name: исходное имя теста (может содержать параметры и пути)
            
        Returns:
            очищенное имя теста
        """
        # Список тестов LMbench и их короткие имена
        if 'bw_file_rd' in test_name:
            return 'bw_file_rd'
        elif 'lat_fs' in test_name:
            return 'lat_fs'
        elif 'lat_syscall' in test_name:
            return 'lat_syscall'
        elif 'lat_ctx' in test_name:
            return 'lat_ctx'
        elif 'lat_sig' in test_name:
            return 'lat_sig'
        elif 'lat_pipe' in test_name:
            return 'lat_pipe'
        elif 'bw_pipe' in test_name:
            return 'bw_pipe'
        elif 'lat_proc' in test_name:
            return 'lat_proc'
        else:
            # Если не нашли соответствия, берем первое слово
            return test_name.split()[0] if ' ' in test_name else test_name
    
    def extract_unixbench_results(self, copies_key='4'):
        """
        Извлекает результаты UnixBench для указанного количества копий
        
        Args:
            copies_key: ключ количества копий (обычно '4' или '8')
        """
        if self.data['unixbench'] is None:
            return {}
        
        ub_data = self.data['unixbench'].get(copies_key, {})
        tests = ub_data.get('tests', {})
        
        results = {}
        for test_name, test_data in tests.items():
            results[test_name] = {
                'value': test_data['value'],
                'unit': test_data['measure']
            }
        return results
    
    def extract_lmbench_results(self):
        """
        Извлекает результаты LMbench с очисткой имен тестов
        """
        if self.data['lmbench'] is None:
            return {}
        
        cleaned_results = {}
        for test_name, test_data in self.data['lmbench'].items():
            clean_name = self._clean_lmbench_name(test_name)
            # Сохраняем только первое вхождение (обычно они одинаковые)
            if clean_name not in cleaned_results:
                cleaned_results[clean_name] = test_data
            else:
                log.debug(f"Дубликат теста {clean_name}, пропускаем")
        
        return cleaned_results
    
    def extract_perf_results(self):
        """Извлекает результаты perf bench"""
        if self.data['perf_bench'] is None:
            return {}
        return self.data['perf_bench']
    
    def extract_fs_mark_results(self):
        """Извлекает результаты fs_mark (берем максимальные значения)"""
        if self.data['fs_mark'] is None:
            return {}
        
        fs_data = self.data['fs_mark']
        results = {}
        
        # Берем максимальные значения из всех итераций
        for key in ['speed', 'create_avg', 'write_avg', 'fsync_avg']:
            if key in fs_data:
                values = list(fs_data[key].values())
                results[key] = {
                    'value': max(values),
                    'unit': 'ops/sec' if key == 'speed' else 'microseconds',
                    'max_value': max(values),
                    'min_value': min(values),
                    'avg_value': sum(values) / len(values)
                }
        
        return results
    
    def build_subsystem_table(self, copies_key='4'):
        """
        Строит таблицу результатов по подсистемам
        
        Args:
            copies_key: ключ количества копий для UnixBench
        """
        # Загружаем результаты
        ub_results = self.extract_unixbench_results(copies_key)
        lm_results = self.extract_lmbench_results()
        perf_results = self.extract_perf_results()
        fs_results = self.extract_fs_mark_results()
        
        table_data = []
        
        # Обрабатываем каждую подсистему
        for subsystem, tests in self.SUBSYSTEM_MAPPING.items():
            subsystem_results = {}
            
            # UnixBench тесты
            for test_name in tests.get('unixbench', []):
                if test_name in ub_results:
                    subsystem_results[test_name] = ub_results[test_name]
            
            # LMbench тесты
            for test_name in tests.get('lmbench', []):
                if test_name in lm_results:
                    subsystem_results[test_name] = lm_results[test_name]
            
            # Perf тесты
            for test_name in tests.get('perf', []):
                if test_name in perf_results:
                    perf_data = perf_results[test_name]
                    subsystem_results[test_name] = {
                        'value': perf_data['value'],
                        'unit': perf_data['unit']
                    }
            
            # fs_mark тесты
            for test_name in tests.get('fs_mark', []):
                if test_name in fs_results:
                    subsystem_results[test_name] = fs_results[test_name]
            
            table_data.append({
                'subsystem': subsystem,
                'tests_count': len(subsystem_results),
                'results': subsystem_results
            })
        
        return table_data
    
    def create_dataframe(self, copies_key='4'):
        """
        Создает pandas DataFrame со всеми результатами
        
        Args:
            copies_key: ключ количества копий для UnixBench
        """
        ub_results = self.extract_unixbench_results(copies_key)
        lm_results = self.extract_lmbench_results()
        perf_results = self.extract_perf_results()
        fs_results = self.extract_fs_mark_results()
        
        rows = []
        
        # Добавляем UnixBench результаты
        for test_name, test_data in ub_results.items():
            rows.append({
                'source': 'UnixBench',
                'test_name': test_name,
                'subsystem': self._get_subsystem_for_test('unixbench', test_name),
                'value': test_data['value'],
                'unit': test_data['unit']
            })
        
        # Добавляем LMbench результаты
        for test_name, test_data in lm_results.items():
            rows.append({
                'source': 'LMbench',
                'test_name': test_name,
                'subsystem': self._get_subsystem_for_test('lmbench', test_name),
                'value': test_data['value'],
                'unit': test_data['unit']
            })
        
        # Добавляем Perf результаты
        for test_name, test_data in perf_results.items():
            rows.append({
                'source': 'perf bench',
                'test_name': test_name,
                'subsystem': self._get_subsystem_for_test('perf bench', test_name),
                'value': test_data['value'],
                'unit': test_data['unit']
            })
        
        # Добавляем fs_mark результаты
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
            elif source == 'perf bench' and test_name in tests.get('perf', []):
                return subsystem
            elif source == 'fs_mark' and test_name in tests.get('fs_mark', []):
                return subsystem
        return 'other'
    
    def print_summary(self, copies_key='4'):
        """Выводит сводку результатов по подсистемам"""
        table_data = self.build_subsystem_table(copies_key)
        
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
                value = test_data['value']
                unit = test_data['unit']
                
                # Форматирование значения
                if isinstance(value, float):
                    if value > 1000:
                        log.info(f"  {test_name:40}: {value:>15,.2f} {unit}")
                    else:
                        log.info(f"  {test_name:40}: {value:>15.4f} {unit}")
                else:
                    log.info(f"  {test_name:40}: {value:>15} {unit}")
        
        # Создаем DataFrame для анализа
        df = self.create_dataframe(copies_key=copies_key)
        log.debug("\nDataFrame со всеми результатами:")
        log.debug(df.to_string())
    
    def export_to_json(self, output_path=SUBSYSTEM_RESULTS, copies_key='4'):
        """Экспортирует результаты в JSON"""
        table_data = self.build_subsystem_table(copies_key)
        
        # Конвертируем для JSON сериализации
        output_data = {}
        for subsystem_info in table_data:
            subsystem = subsystem_info['subsystem']
            results = subsystem_info['results']
            
            output_data[subsystem] = {}
            for test_name, test_data in results.items():
                output_data[subsystem][test_name] = test_data
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=4, ensure_ascii=False)
        
        log.info(f"Результаты экспортированы в {output_path}")

        