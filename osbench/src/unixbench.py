import re
import pandas as pd

from os import chdir, path, listdir

from lib import Test, system, status_check
from osb_logger import log
from config.conf import (
    LOW_CONC,
    HIGH_CONC,
    STEP,
    MAIN_DIR,
    TEST_NAMES,
    TEST_MEASURE,
    REGEXP_OVERALL_SCORE,
    REGEXP_PARALLEL_COPIES,
    REGEXP_PARSERS
)



class UnixBenchParser:
    def __init__(self, 
                 report_dir, 
                 report_filename):
        
        self.__report_dir = report_dir
        self.__report_filename = report_filename
        self.__raw_dict = {}
        self.__raw_tables = {}
        self.overall_score = None
        self.parallel_copies = None
        

    def _find_result_file_without_extension(self, report_dir):
        """
        Находит файл с результатами без расширения
        """
        if not path.exists(report_dir):
            log.error(f"Директория не существует: {report_dir}")
            return None
        
        # Получаем все элементы в директории
        all_items = listdir(report_dir)
        
        # Фильтруем: оставляем только файлы/директории без расширения
        # Исключаем .html и .log файлы
        result_files = []
        for item in all_items:
            item_path = path.join(report_dir, item)
            log.debug(item_path)
            
            # Проверяем, что это не файл с расширением .html или .log
            if not item.endswith('.html') and not item.endswith('.log'):
                result_files.append(item)
        
        if not result_files:
            log.error(f"Не найдены файлы без расширения в {report_dir}")
            return None
        
        # Если нашли несколько, берем последний по времени создания
        if len(result_files) > 1:
            files_with_time = []
            for f in result_files:
                f_path = path.join(report_dir, f)
                ctime = path.getctime(f_path)
                files_with_time.append((f, ctime))
            
            latest_file = max(files_with_time, key=lambda x: x[1])[0]
            log.info(f"Найдено несколько файлов. Выбран последний: {latest_file}")
            return latest_file
        
        result_file = result_files[0]
        log.info(f"Найден файл с результатами: {result_file}")
        return result_file

    def parse(self):
        """
        Основной метод парсинга файла с результатами
        """
        filepath = f"{self.__report_dir}/{self.__report_filename}"
        
        try:
            with open(filepath, 'r') as file:
                text = file.read()
                
                # Парсим общую информацию
                self._parse_system_info(text)
                
                # Парсим результаты тестов
                self._parse_benchmark_results(text)
                
                # Создаем DataFrame'ы
                self._create_dataframes()
                
                return True
                
        except FileNotFoundError:
            log.error(f"Файл не найден: {filepath}")
            return False
        except Exception as e:
            log.error(f"Ошибка при парсинге: {e}")
            return False
    
    def _parse_system_info(self, text):
        """
        Парсинг общей информации
        """
        # Количество параллельных копий
        copies_match = re.search(REGEXP_PARALLEL_COPIES, text)
        if copies_match:
            self.parallel_copies = int(copies_match.group(1))
            log.info(f"Параллельных копий: {self.parallel_copies}")
                
        # Общий score
        score_match = re.search(REGEXP_OVERALL_SCORE, text)
        if score_match:
            self.overall_score = float(score_match.group(1))
            log.info(f"Общий Score: {self.overall_score}")
    
    def _parse_benchmark_results(self, text):
        """
        Парсинг результатов бенчмарков
        """
        parallel_copies_value = float(self.parallel_copies) if self.parallel_copies is not None else 1.0

        for test, regexp in REGEXP_PARSERS.items():
            result_tuples = re.findall(regexp, text)
            
            if result_tuples:
                self.__raw_dict[test] = {
                    'parallel_threads': [parallel_copies_value],
                    'value': [float(result_tuple[0]) for result_tuple in result_tuples],
                    'time': [float(result_tuple[1]) for result_tuple in result_tuples],
                    'samples': [int(result_tuple[2]) for result_tuple in result_tuples],
                }
                
                # Выводим информацию для отладки
                log.debug(f"{test}: {result_tuples[0][0]} {TEST_MEASURE[test]} "
                      f"(time: {result_tuples[0][1]}s, samples: {result_tuples[0][2]})")
            else:
                log.warning(f"Предупреждение: Тест '{test}' не найден в файле")
                self.__raw_dict[test] = {
                    'parallel_threads': [parallel_copies_value],
                    'value': [0.0],
                    'time': [0.0],
                    'samples': [0],
                }
    
    def _create_dataframes(self):
        """
        Создание DataFrame'ов из распарсенных данных
        """
        parallel_copies_value = float(self.parallel_copies) if self.parallel_copies is not None else 0.0

        for test in TEST_NAMES:
            if test in self.__raw_dict:
                self.__raw_tables[test] = pd.DataFrame(self.__raw_dict[test])
            else:
                # Создаем пустой DataFrame если тест отсутствует
                self.__raw_tables[test] = pd.DataFrame({
                    'parallel_threads': [parallel_copies_value],
                    'value': [0.0],
                    'time': [0.0],
                    'samples': [0]
                })
    
    def get_dataframe(self, test_name):
        """
        Получить DataFrame для конкретного теста
        """
        return self.__raw_tables.get(test_name)
    
    def get_all_results(self):
        """
        Получить все результаты в виде одного DataFrame
        """
        all_data = []
        for test in TEST_NAMES:
            if test in self.__raw_dict and self.__raw_dict[test]['value'][0] > 0:
                all_data.append({
                    'test_name': test,
                    'measure': TEST_MEASURE.get(test, 'unknown'),
                    'value': self.__raw_dict[test]['value'][0],
                    'time': self.__raw_dict[test]['time'][0],
                    'samples': self.__raw_dict[test]['samples'][0],
                })
        return pd.DataFrame(all_data)
    
    def summary_info(self):
        """
        Вывод краткой сводки результатов
        """
        log.info("\n" + "="*60)
        log.info("КРАТКАЯ СВОДКА РЕЗУЛЬТАТОВ")
        log.info("="*60)
        
        if self.overall_score:
            log.info(f"Общий индекс производительности: {self.overall_score:.2f}")
        
        if self.parallel_copies:
            log.info(f"Параллельных копий: {self.parallel_copies}")
        
        log.info("\nРезультаты тестов:")
        log.info("-" * 60)
        
        for test in TEST_NAMES:
            if test in self.__raw_dict and self.__raw_dict[test]['value'][0] > 0:
                value = self.__raw_dict[test]['value'][0]
                measure = TEST_MEASURE.get(test, '')
                log.info(f"{test:20}: {value:>12.2f} {measure}")



class UnixBench(Test, UnixBenchParser):

    def __init__(self,
                 report_dir=None, 
                 report_filename=None,
                 low_concurrency=LOW_CONC,
                 high_concurrency=HIGH_CONC,
                 step=STEP):
        
        if report_dir is None:
            self.__report_dir = f"{MAIN_DIR}/benchmarks/UnixBench/byte-unixbench/UnixBench/results"
        else:
            self.__report_dir = report_dir

        # Находим имя файла автоматически
        if report_filename is None:
            report_filename = self._find_result_file_without_extension(self.__report_dir)

        UnixBenchParser.__init__(self, self.__report_dir, report_filename)

        self.low_concurrency = low_concurrency
        self.high_concurrency = high_concurrency
        self.step = step


    @status_check  
    def start_test(self):

        log.info("Запуск UnixBench")
        ub_dir = f"{MAIN_DIR}/benchmarks/UnixBench/byte-unixbench/UnixBench/"
        concurrency = [self.low_concurrency] + list(range(self.step, self.high_concurrency, self.step))
        run_cmd_args = ' '.join(f"-c {c}" for c in concurrency)

        chdir(ub_dir)
        system.leave_command("sudo chmod +x Run", returncode=True)
        result, code = system.leave_command(f"./Run {run_cmd_args}", returncode=True)

        log.debug(f"code = {code}, type = {type(code)}")

        if code:
            log.info(result)
            log.info("UnixBench: - тестирование завершено успешно")
            return result, True
        else:
            log.error(result)
            log.error("UnixBench: - тестирование провалено")
            return result, False


    def get_results(self):
        # Проверяем существование файла перед парсингом
        filepath = f"{self.__report_dir}/{self.__report_filename}"
        if not path.exists(filepath):
            log.error(f"Файл с результатами не найден: {filepath}")
            return None
    
        if self.parse():
            self.summary_info()
            
            # Получаем DataFrame с результатами
            results_df = self.get_all_results()
            log.info("\n" + "="*60)
            log.info("DataFrame со всеми результатами:")
            log.info(f"\n{results_df}") 
            return results_df
        else:
            log.error("Не удалось распарсить результаты")
            return None
        
        
    
        

        
