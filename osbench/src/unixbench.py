import re
import json
import pandas as pd

from os import chdir, path, listdir, makedirs

from lib import Test, system, status_check, Writer
from osb_logger import log, Colors
from config.conf import (
    MAIN_DIR,
    TEST_MEASURE,
    REGEXP_OVERALL_SCORE,
    REGEXP_PARALLEL_COPIES,
    REGEXP_PARSERS,
    RESULTS_MAIN_DIR,
    RESULT_UB_NAME,
    RESULTS_STATUS,
    CONCURRENCY
)



class UnixBenchParser:
    def __init__(self, 
                 report_dir, 
                 report_filename):
        
        self._report_dir = report_dir
        self._report_filename = report_filename
        self._results_by_copies = {}  
              

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
        filepath = f"{self._report_dir}/{self._report_filename}"
        
        try:
            with open(filepath, 'r') as file:
                content = file.read()
                
                # Ищем все вхождения "BYTE UNIX Benchmarks"
                iterations_text = self._split_into_iterations(content)
                
                for iter_text in iterations_text:
                    self._parse_single_iteration(iter_text)
                
                return True
                
        except FileNotFoundError:
            log.error(f"Файл не найден: {filepath}")
            return False
        except Exception as e:
            log.error(f"Ошибка при парсинге: {e}")
            return False

    def _split_into_iterations(self, content):
        """
        Разбивает содержимое файла на отдельные итерации
        """
        pattern = r"Benchmark Run.*?(?=Benchmark Run|$)"
        iterations = re.findall(pattern, content, re.DOTALL)
        
        if not iterations:
            return [content]
        
        return iterations

    def _parse_single_iteration(self, text):
        """
        Парсинг одной итерации (одного значения параллельных копий)
        """
        # Парсим parallel_copies для этой итерации
        copies_match = re.search(REGEXP_PARALLEL_COPIES, text)
        if not copies_match:
            log.warning("Не найдено значение параллельных копий, пропускаем итерацию")
            return
        
        parallel_copies = int(copies_match.group(1))
        copies_key = str(parallel_copies)  
        
        log.info(f"Парсинг результатов для {parallel_copies} параллельных копий")
        
        # Парсим overall score
        score_match = re.search(REGEXP_OVERALL_SCORE, text)
        overall_score = float(score_match.group(1)) if score_match else None
        
        # Инициализируем структуру для этого количества копий
        self._results_by_copies[copies_key] = {
            'overall_score': overall_score,
            'tests': {}
        }
        
        # Парсим результаты тестов
        for test, regexp in REGEXP_PARSERS.items():
            result_tuples = re.findall(regexp, text)
            
            if result_tuples:
                result = result_tuples[0]
                self._results_by_copies[copies_key]['tests'][test] = {
                    'measure': TEST_MEASURE.get(test, 'unknown'),
                    'value': float(result[0]),
                    'time': float(result[1]),
                    'samples': int(result[2])
                }
                log.debug(f"  {test}: {result[0]} {TEST_MEASURE.get(test, '')} "
                          f"(time: {result[1]}s, samples: {result[2]})")
            else:
                log.warning(f"Тест '{test}' не найден для {parallel_copies} копий")
                self._results_by_copies[copies_key]['tests'][test] = {
                    'measure': TEST_MEASURE.get(test, 'unknown'),
                    'value': 0.0,
                    'time': 0.0,
                    'samples': 0
                }
        
    def get_results_by_copies(self):
        """
        Получить результаты, сгруппированные по количеству параллельных копий
        """
        return self._results_by_copies
    
    def get_results_as_dataframe(self):
        """
        Получить все результаты в виде DataFrame (для удобного анализа)
        """
        all_data = []
        for copies, data in self._results_by_copies.items():
            for test_name, test_data in data['tests'].items():
                all_data.append({
                    'parallel_copies': int(copies),
                    'test_name': test_name,
                    'measure': test_data['measure'],
                    'value': test_data['value'],
                    'time': test_data['time'],
                    'samples': test_data['samples'],
                    'overall_score': data['overall_score']
                })
        return pd.DataFrame(all_data)
    
    def summary_info(self):
        """
        Вывод краткой сводки результатов по всем параллельным копиям
        """
        log.info("="*60)
        log.info("КРАТКАЯ СВОДКА РЕЗУЛЬТАТОВ")
        log.info("="*60)
        
        for copies, data in sorted(self._results_by_copies.items(), key=lambda x: int(x[0])):
            log.info(f"--- {copies} параллельных копий ---")
            log.info(f"Общий индекс производительности: {data['overall_score']:.2f}")
            log.info("Результаты тестов:")
            
            for test_name, test_data in data['tests'].items():
                if test_data['value'] > 0:
                    log.info(f"  {test_name:20}: {test_data['value']:>12.2f} {test_data['measure']}")



class UnixBench(Test, UnixBenchParser):

    def __init__(self,
                 report_dir=None, 
                 report_filename=None):
        
        if report_dir is None:
            self._report_dir = f"{MAIN_DIR}/benchmarks/UnixBench/byte-unixbench/UnixBench/results"
        else:
            self._report_dir = report_dir

        self.test_success = False
        self._report_filename = report_filename
        self.writer = Writer(file_name=RESULTS_STATUS)

        UnixBenchParser.__init__(self, self._report_dir, self._report_filename)

    
    def get_report_dir(self):
        return self._report_dir
    
    def get_report_filename(self):
        return self._report_filename


    @status_check  
    def start_test(self):

        log.info("Запуск UnixBench")
        ub_dir = f"{MAIN_DIR}/benchmarks/UnixBench/byte-unixbench/UnixBench/"
        run_cmd_args = ' '.join(f"-c {c}" for c in CONCURRENCY)

        chdir(ub_dir)
        system.leave_command("sudo chmod +x Run", returncode=True)
        result, code = system.leave_command(f"./Run {run_cmd_args}", returncode=True)

        self.writer.wrs(cl=self.__class__,
                        method=self.start_test.__name__,
                        test="ALL_TESTS",
                        status=code,
                        message=f"{'SUCCESS' if code else 'FAILURE'}"
)

        log.debug(f"code = {code}, type = {type(code)}")

        if code:
            log.info(result)
            log.info("UnixBench: - тестирование завершено успешно")
            log.debug(f"{Colors.GREEN}Все тесты успешно пройдены: {code}{Colors.RESET}")
            self.test_success = True
            return result, True
        else:
            log.error(result)
            log.error("UnixBench: - тестирование провалено")
            log.debug(f"{Colors.RED}Статусы: {code}{Colors.RESET}")
            self.test_success = False
            return result, False


    def get_results(self):
        """
        Получить результаты и сохранить в JSON
        """
        if not self.test_success:
            log.critical(f"{Colors.RED}UnixBench: тесты не были успешно завершены, сбор результатов пропущен{Colors.RESET}")
            return True, False
        
        # Находим имя файла автоматически
        if self._report_filename is None:
            self._report_filename = self._find_result_file_without_extension(self._report_dir)
            
        if self._report_filename is None:
            log.error("Не удалось определить файл с результатами")
            return None
        
        self._results_by_copies = {} 
        
        filepath = f"{self._report_dir}/{self._report_filename}"
        if not path.exists(filepath):
            log.error(f"Файл с результатами не найден: {filepath}")
            return None

        if self.parse():
            self.summary_info()
            
            # Получаем структуру результатов
            results_dict = self.get_results_by_copies()
            
            # Создаём директорию, если её нет
            makedirs(RESULTS_MAIN_DIR, exist_ok=True)
            
            # Сохраняем в JSON
            json_path = f"{RESULTS_MAIN_DIR}/{RESULT_UB_NAME}"
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(results_dict, f, indent=4, ensure_ascii=False)
            log.info(f"Результаты сохранены в {json_path}")
                        
            return results_dict
        else:
            log.error("Не удалось распарсить результаты")
            return None
        
        
    
        

        
