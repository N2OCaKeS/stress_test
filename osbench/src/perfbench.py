import re
import json

from os import makedirs
from pathlib import Path

from lib import Test, system, status_check, Writer
from osb_logger import log, Colors
from config.conf import (
    MAIN_DIR,
    RESULTS_MAIN_DIR,
    RESULT_PERF_BENCH_NAME,
    RESULTS_STATUS,
    CONCURRENCY
)


class PerfBench(Test):
    """
    Perf Bench
    """
    def __init__(self,
                 report_filename=None):
        
        self.test_success = False
        self.results_dir = f"{RESULTS_MAIN_DIR}"
        self.results_file = f"{self.results_dir}/perf_bench_result.txt"
        self.writer = Writer(file_name=RESULTS_STATUS)
        makedirs(self.results_dir, exist_ok=True)

        if report_filename is None:
            self._report_filename = f"{RESULTS_MAIN_DIR}/perf_bench_results.json"
        else:
            self._report_filename = report_filename

    def run_perf_test(self, test_args: str, concur: int) -> tuple:
        """
        Запуск отдельного теста perf bench с указанной параллельностью
        """
        if "sched messaging" in test_args:
            cmd = f"perf bench {test_args} -p {concur}"
        elif "sched pipe" in test_args:
            cmd = f"perf bench {test_args} -T"  
        elif "futex" in test_args:
            cmd = f"perf bench {test_args} -t {concur}"
        elif "epoll" in test_args:
            cmd = f"perf bench {test_args} -t {concur}"
        else:
            cmd = f"perf bench {test_args}"
        
        result, code = system.leave_command(cmd, returncode=True, console=False)
        return result, code

    #@status_check  
    def start_test(self):
        """
        Запуск всех тестов perf bench с разными уровнями concurrency
        """
        log.info("Запуск Perf Bench")
        
        tests = [
            # ========== ПЛАНИРОВЩИК И IPC ==========
            ("sched pipe", "sched pipe"),           # pipe через планировщик
            ("sched messaging", "sched messaging"), # IPC через планировщик
            
            # ========== ПАМЯТЬ (ИСКЛЮЧЕНА) ==========
            # ("memcpy", "mem memcpy"),              # зависит от железа
            
            # ========== СИНХРОНИЗАЦИЯ ==========
            ("futex hash", "futex hash"),           # хэш-таблица с futex
            ("futex wake", "futex wake"),           # пробуждение futex
            ("futex requeue", "futex requeue"),     # перемещение очереди futex
            
            # ========== СОБЫТИЯ ==========
            ("epoll wait", "epoll wait"),           # ожидание epoll
            ("epoll ctl", "epoll ctl"),             # управление epoll
        ]
        
        status_code_dict = {}
        
        # Очищаем файл результатов
        with open(self.results_file, 'w') as f:
            f.write("Perf Bench Results\n")
            f.write(f"Started: {system.leave_command('date', returncode=True, console=False)[0]}\n")
            f.write(f"{'='*60}\n\n")
        
        for concur in CONCURRENCY:
            log.debug(f"Запуск Perf Bench с {concur} параллельными потоками")
            for display_name, test_args in tests:
                log.debug(f"Running: {display_name} (concurrency={concur})...")
                
                output, code = self.run_perf_test(test_args, concur)
                key = f"{display_name}_{concur}"
                status_code_dict[key] = code
                
                # Сохраняем вывод в файл
                with open(self.results_file, 'a') as f:
                    f.write(f"\n{'='*60}\n")
                    f.write(f"Test: {display_name} (concurrency={concur})\n")
                    f.write(f"{'='*60}\n")
                    f.write(output)
                    f.write(f"\n{'='*60}\n\n")
                
                self.writer.wrs(cl=self.__class__,
                                method=self.start_test.__name__,
                                test=f"{display_name} {concur}",
                                status=code)
        
        if all(code for code in status_code_dict.values()):
            log.debug("Perf Bench: тестирование завершено успешно")
            log.info(f"{Colors.GREEN}Все тесты успешно пройдены: {status_code_dict}{Colors.RESET}")
            self.test_success = True
            return True, True
        else:
            failed_tests = [name for name, code in status_code_dict.items() if not code]
            log.critical(f"Perf Bench: тестирование провалено. Проваленные тесты: {failed_tests}")
            log.error(f"{Colors.RED}Статусы: {status_code_dict}{Colors.RESET}")
            self.test_success = False
            return True, False

    #@status_check
    def get_results(self):
        """
        Получить результаты и сохранить в JSON с группировкой по concurrency
        """
        if not self.test_success:
            log.critical(f"{Colors.RED}Perf Bench: тесты не были успешно завершены, сбор результатов пропущен{Colors.RESET}")
            return True, False
        
        log.debug("Сохранение результатов Perf Bench")
        
        # Проверяем существование файла с результатами
        if not Path(self.results_file).exists():
            log.error(f"Файл с результатами не найден: {self.results_file}")
            return True, False
        
        try:
            with open(self.results_file, 'r') as f:
                content = f.read()
            
            makedirs(RESULTS_MAIN_DIR, exist_ok=True)
            
            # Результаты с группировкой по concurrency
            results_by_concurrency = {str(concur): {} for concur in CONCURRENCY}
            test_blocks = re.findall(r'Test: (.+?)\n=+\n(.*?)\n=+', content, re.DOTALL)
            
            for test_block_name, result_text in test_blocks:
                test_block_name = test_block_name.strip()
                
                concur_match = re.search(r'concurrency[=:](\d+)', test_block_name, re.IGNORECASE)
                
                if concur_match:
                    concur = concur_match.group(1)
                    clean_test_name = re.sub(r'\s*\(concurrency[=:]\d+\)', '', test_block_name).strip()
                    if 'sched messaging' in clean_test_name:
                        clean_test_name = 'sched messaging'
                else:
                    log.warning(f"Не удалось определить concurrency для теста: {test_block_name}")
                    continue
                
                # Парсим значение в зависимости от типа теста
                parsed_value = self._parse_test_value(clean_test_name, result_text)
                
                if parsed_value:
                    results_by_concurrency[concur][clean_test_name] = parsed_value
            
            json_path = f"{RESULTS_MAIN_DIR}/{RESULT_PERF_BENCH_NAME}"
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(results_by_concurrency, f, indent=4, ensure_ascii=False)
            
            log.debug(f"Perf Bench: результаты сохранены в {json_path}")
            log.debug(f"Собрано результатов для {len(results_by_concurrency)} уровней concurrency")
            
            return True, True
            
        except Exception as e:
            log.critical(f"Perf Bench: ошибка при сохранении результатов: {e}")
            import traceback
            log.critical(traceback.format_exc())
            return True, False

    def _parse_test_value(self, test_name, result_text):
        """
        Парсит значение теста в зависимости от его типа
        """
        # sched pipe и sched messaging
        if 'sched' in test_name:
            match = re.search(r'Total time:\s*([\d\.]+)\s*\[sec\]', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "seconds"}
            numbers = re.findall(r'([\d\.]+)\s+\[sec\]', result_text)
            if numbers:
                return {"value": float(numbers[0]), "unit": "seconds"}
            match = re.search(r'Time:\s*([\d\.]+)\s*seconds', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "seconds"}
            numbers = re.findall(r'([\d\.]+)\s+seconds', result_text)
            if numbers:
                return {"value": float(numbers[0]), "unit": "seconds"}
        
        # futex hash (ops/sec) - берем первое значение потока или среднее
        elif 'futex hash' in test_name:
            # Ищем значение для thread 0
            match = re.search(r'\[\s*thread\s+0\]\s+.*?\s+(\d+)\s+ops/sec', result_text, re.DOTALL)
            if match:
                return {"value": float(match.group(1)), "unit": "ops/sec"}
            # Ищем среднее значение
            match = re.search(r'Averaged\s+(\d+)\s+operations/sec', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "ops/sec"}
            match = re.search(r'([\d,]+)\s+ops/sec', result_text)
            if match:
                value = match.group(1).replace(',', '')
                return {"value": float(value), "unit": "ops/sec"}
            numbers = re.findall(r'([\d\.]+)', result_text)
            if numbers:
                return {"value": float(numbers[-1]), "unit": "ops/sec"}
        
        # futex wake и futex requeue (миллисекунды)
        elif 'futex wake' in test_name or 'futex requeue' in test_name:
            # Ищем среднее значение
            match = re.search(r'Wokeup\s+\d+\s+of\s+\d+\s+threads\sin\s+([\d\.]+)\s+ms', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "milliseconds"}
            match = re.search(r'Requeued\s+\d+\s+of\s+\d+\s+threads\sin\s+([\d\.]+)\s+ms', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "milliseconds"}
            match = re.search(r'([\d\.]+)\s+milliseconds', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "milliseconds"}
            numbers = re.findall(r'([\d\.]+)', result_text)
            if numbers:
                return {"value": float(numbers[-1]), "unit": "milliseconds"}
        
        # epoll wait и epoll ctl (ops/sec или operations/sec)
        elif 'epoll' in test_name:
            # Для epoll wait
            match = re.search(r'\[\s*thread\s+0\]\s+.*?\s+(\d+)\s+ops/sec', result_text, re.DOTALL)
            if match:
                return {"value": float(match.group(1)), "unit": "operations/sec"}
            # Для epoll ctl - берем ADD operations
            match = re.search(r'Averaged\s+(\d+)\s+ADD\s+operations', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "operations/sec"}
            match = re.search(r'([\d,]+)\s+(?:ops|operations)/sec', result_text)
            if match:
                value = match.group(1).replace(',', '')
                return {"value": float(value), "unit": "operations/sec"}
            numbers = re.findall(r'([\d\.]+)', result_text)
            if numbers:
                return {"value": float(numbers[-1]), "unit": "operations/sec"}
        
        # memcpy (GB/sec)
        elif 'memcpy' in test_name:
            match = re.search(r'([\d\.]+)\s+GB/sec', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "GB/sec"}
            numbers = re.findall(r'([\d\.]+)', result_text)
            if numbers:
                return {"value": float(numbers[-1]), "unit": "GB/sec"}
        
        else:
            # Общий парсинг: ищем число с единицей измерения
            units = ['seconds', 'milliseconds', 'microseconds', 'ops/sec', 'operations/sec', 'GB/sec', 'MB/s']
            for unit in units:
                match = re.search(r'([\d,\.]+)\s+' + unit, result_text)
                if match:
                    value = match.group(1).replace(',', '')
                    return {"value": float(value), "unit": unit}
            
            # Если ничего не нашли, берем первое число
            numbers = re.findall(r'([\d\.]+)', result_text)
            if numbers:
                return {"value": float(numbers[0]), "unit": "unknown"}
        
        return None