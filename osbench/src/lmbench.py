import re
import json

from os import makedirs
from pathlib import Path

from lib import Test, system, status_check
from osb_logger import log, Colors
from config.conf import (
    MAIN_DIR,
    RESULTS_MAIN_DIR,
    RESULT_LMBENCH_NAME
)


class LMBench(Test):
    """
    LMbench
    """
    def __init__(self,
                 report_filename=None):
        
        self.test_success = False
        self.lmbench_dir = f"{MAIN_DIR}/benchmarks/LMbench/lmbench/"
        self.bin_path = f"{self.lmbench_dir}/bin/x86_64-linux-gnu"
        self.results_dir = f"{self.lmbench_dir}/results"
        self.results_file = f"{self.results_dir}/lmbench_result.txt"
        self.test_dir = f"{self.lmbench_dir}/testdir"
        self.test_file = f"{self.test_dir}/testfile"
        makedirs(self.results_dir, exist_ok=True)
        makedirs(self.test_dir, exist_ok=True)

        if report_filename is None:
            self._report_filename = f"{MAIN_DIR}/benchmarks/LMbench/lmbench/"
        else:
            self._report_filename = report_filename

    def run_test(self, test_name, args):
        """
        Запуск отдельного теста
        """
        if args:
            cmd = f"{self.bin_path}/{test_name} {args}".strip()
        else:
            cmd = f"{self.bin_path}/{test_name}".strip()

        result, code = system.leave_command(cmd, returncode=True)

        with open(self.results_file, 'a') as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Test: {test_name} {args}\n")
            f.write(f"{'='*60}\n")
            f.write(result)
            f.write(f"\n{'='*60}\n\n")

        return result, code


    @status_check  
    def start_test(self):

        log.info("Запуск LMbench")
        log.warning("=" * 60)
        log.warning("⚠️ ПРИМЕЧАНИЕ:")
        log.warning("LMbench выводит результаты тестов в stderr")
        log.warning("Это НЕ ошибки, а нормальное поведение бенчмарка")
        log.warning("=" * 60)
        status_code_dict = {}

        log.info(f"Создание тестового файла: {self.test_file}")
        system.leave_command(f"dd if=/dev/zero of={self.test_file} bs=1M count=100", returncode=True)

        tests = [
            # Ядро
            ("lat_syscall", "null"),
            ("lat_syscall", "read"),
            ("lat_syscall", "write"),
            ("lat_ctx", "-s 0 2 4 8 16 24 32 64 128"),
            ("lat_sig", "install"),
            ("lat_sig", "catch"),
            
            # Память
            ("lat_mem_rd", "16384 512"),
            ("bw_mem", "64M cp"),
            ("bw_mem", "64M rd"),
            ("bw_mem", "64M wr"),
            ("bw_mem", "16384M cp"),
            
            # IPC
            ("lat_pipe", ""),
            ("bw_pipe", ""),
            ("lat_proc", "fork"),
            ("lat_proc", "exec"),
            
            # Файловая система
            ("lat_fs", "0K"),
            ("lat_fs", "10K"),
            ("bw_file_rd", f"65536 open2close {self.test_file}"),
        ]

        with open(self.results_file, 'w') as f:
            f.write(f"LMbench Results\n")
            f.write(f"Started: {system.leave_command('date', returncode=True)[0]}\n")
            f.write(f"{'='*60}\n\n")

        for test_name, args in tests:
            log.info(f"Running {test_name} {args}...")
            output, code = self.run_test(test_name, args)
            status_code_dict[test_name] = code

        if all(code for code in status_code_dict.values()):
            log.info("LMbench: - тестирование завершено успешно")
            log.debug(f"{Colors.GREEN}Все тесты успешно пройдены: {status_code_dict}{Colors.RESET}")
            self.test_success = True
            return True, True
        else:
            failed_tests = [name for name, code in status_code_dict.items() if not code]
            log.critical(f"LMbench: - тестирование провалено. Проваленные тесты: {failed_tests}")
            log.debug(f"{Colors.RED}Статусы: {status_code_dict}{Colors.RESET}")
            self.test_success = False
            return True, False

        
    @status_check
    def get_results(self):
        """
        Получить результаты и сохранить в JSON
        """

        if not self.test_success:
            log.critical(f"{Colors.RED}LMbench: тесты не были успешно завершены, сбор результатов пропущен{Colors.RESET}")
            return True, False
        
        log.info("Сохранение результатов LMbench")
        
        # Проверяем существование файла с результатами
        if not Path(self.results_file).exists():
            log.error(f"Файл с результатами не найден: {self.results_file}")
            return True, False
        
        try:
            with open(self.results_file, 'r') as f:
                content  = f.read()
            
            makedirs(RESULTS_MAIN_DIR, exist_ok=True)
            
            results = {}
            
            # Разбиваем на тесты
            test_blocks = re.findall(r'Test: (.+?)\n=+\n(.*?)\n=+', content, re.DOTALL)
            
            for test_name, result_text in test_blocks:
                test_name = test_name.strip()
                
                # Определяем тип теста и единицы измерения
                if 'lat_mem_rd' in test_name:
                    numbers = re.findall(r'[\d\.]+\s+([\d\.]+)', result_text)
                    if numbers:
                        results[test_name] = {
                            "value": float(numbers[-1]),
                            "unit": "nanoseconds"
                        }
                        
                elif 'lat_ctx' in test_name:
                    numbers = re.findall(r'(\d+)\s+([\d\.]+)', result_text)
                    if numbers:
                        results[test_name] = {
                            "value": float(numbers[-1][1]),
                            "unit": "microseconds"
                        }
                        
                elif 'lat_fs' in test_name:
                    match = re.search(r'10k\s+(\d+)', result_text, re.IGNORECASE)
                    if match:
                        results[test_name] = {
                            "value": int(match.group(1)),
                            "unit": "operations/sec"
                        }
                        
                elif 'bw_mem' in test_name:
                    numbers = re.findall(r'[\d\.]+\s+([\d\.]+)', result_text)
                    if numbers:
                        results[test_name] = {
                            "value": float(numbers[-1]),
                            "unit": "MB/s"
                        }
                        
                elif 'bw_file_rd' in test_name:
                    match = re.search(r'[\d\.]+\s+([\d\.]+)', result_text)
                    if match:
                        results[test_name] = {
                            "value": float(match.group(1)),
                            "unit": "MB/s"
                        }
                        
                elif 'lat_pipe' in test_name:
                    match = re.search(r'([\d\.]+)\s+microseconds', result_text)
                    if match:
                        results[test_name] = {
                            "value": float(match.group(1)),
                            "unit": "microseconds"
                        }
                        
                elif 'bw_pipe' in test_name:
                    match = re.search(r'([\d\.]+)\s+MB/sec', result_text)
                    if match:
                        results[test_name] = {
                            "value": float(match.group(1)),
                            "unit": "MB/s"
                        }
                        
                elif 'lat_proc' in test_name:
                    match = re.search(r'([\d\.]+)\s+microseconds', result_text)
                    if match:
                        results[test_name] = {
                            "value": float(match.group(1)),
                            "unit": "microseconds"
                        }
                        
                elif 'lat_sig' in test_name:
                    match = re.search(r'([\d\.]+)\s+microseconds', result_text)
                    if match:
                        results[test_name] = {
                            "value": float(match.group(1)),
                            "unit": "microseconds"
                        }
                        
                elif 'lat_syscall' in test_name:
                    match = re.search(r'([\d\.]+)\s+microseconds', result_text)
                    if match:
                        results[test_name] = {
                            "value": float(match.group(1)),
                            "unit": "microseconds"
                        }
                else:
                    numbers = re.findall(r'([\d\.]+)', result_text)
                    if numbers:
                        results[test_name] = {
                            "value": float(numbers[0]),
                            "unit": "unknown"
                        }
            
            makedirs(RESULTS_MAIN_DIR, exist_ok=True)
            json_path = f"{RESULTS_MAIN_DIR}/{RESULT_LMBENCH_NAME}"
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=4, ensure_ascii=False)
            
            log.info(f"LMbench: - результаты сохранены в {json_path}")
            log.info(f"Собрано результатов: {len(results)}")
            
            return True, True
            
        except Exception as e:
            log.critical(f"LMbench: - ошибка при сохранении результатов: {e}")
            import traceback
            log.critical(traceback.format_exc())
            return True, False

