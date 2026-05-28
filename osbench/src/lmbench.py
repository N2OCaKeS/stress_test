import re
import json

from os import makedirs
from pathlib import Path

from lib import Test, system, status_check, Writer
from osb_logger import log, Colors
from config.conf import (
    MAIN_DIR,
    RESULTS_MAIN_DIR,
    RESULT_LMBENCH_NAME,
    RESULTS_STATUS,
    LOW_CONC,
    HIGH_CONC,
    STEP
)


class LMBench(Test):
    """
    LMbench
    """
    def __init__(self,
                 report_filename=None,
                 low_concurrency=LOW_CONC,
                 high_concurrency=HIGH_CONC,
                 step=STEP):
        
        self.test_success = False
        self.lmbench_dir = f"{MAIN_DIR}/benchmarks/LMbench/lmbench"
        self.bin_path = f"{self.lmbench_dir}/bin/x86_64-linux-gnu"
        self.results_dir = f"{self.lmbench_dir}/results"
        self.results_file = f"{self.results_dir}/lmbench_result.txt"
        self.test_dir = f"{self.lmbench_dir}/testdir"
        self.test_file = f"{self.test_dir}/testfile"
        self.writer = Writer(file_name=RESULTS_STATUS)
        makedirs(self.results_dir, exist_ok=True)
        makedirs(self.test_dir, exist_ok=True)

        self.low_concurrency = low_concurrency
        self.high_concurrency = high_concurrency
        self.step = step
        self.concurrency = [self.low_concurrency] + list(range(self.step, self.high_concurrency, self.step))

        if report_filename is None:
            self._report_filename = f"{MAIN_DIR}/benchmarks/LMbench/lmbench/"
        else:
            self._report_filename = report_filename

    def run_test(self, test_name, concur, args):
        """
        Запуск отдельного теста
        """
        if args:
            cmd = f"{self.bin_path}/lmbench -P {concur} {test_name} {args}".strip()
        else:
            cmd = f"{self.bin_path}/{test_name}".strip()

        result, code = system.leave_command(cmd, returncode=True)

        with open(self.results_file, 'a') as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Test: {test_name} {args} (concurrency={concur})\n")
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
            # ========== ЯДРО И СИСТЕМНЫЕ ВЫЗОВЫ ==========
            ("lat_syscall", "null"),      # системный вызов getppid
            ("lat_syscall", "read"),      # системный вызов read /dev/zero
            ("lat_syscall", "write"),     # системный вызов write /dev/null
            ("lat_ctx", "-s 0 2 4 8 16 24 32 64 128"),  # контекст переключения процессов
            ("lat_sig", "install"),       # установка обработчика сигнала
            ("lat_sig", "catch"),         # перехват сигнала
            
            # ========== ПАМЯТЬ (ИСКЛЮЧЕНЫ - ЗАВИСЯТ ОТ ЖЕЛЕЗА) ==========
            # ("lat_mem_rd", "16384 512"),
            # ("bw_mem", "64M cp"),
            # ("bw_mem", "64M rd"),
            # ("bw_mem", "64M wr"),
            # ("bw_mem", "16384M cp"),
            
            # ========== IPC И ПРОЦЕССЫ ==========
            ("lat_pipe", ""),             # задержка в pipe
            ("bw_pipe", ""),              # пропускная способность pipe
            ("lat_proc", "fork"),         # время fork
            ("lat_proc", "exec"),         # время exec
            
            # ========== ФАЙЛОВАЯ СИСТЕМА ==========
            ("lat_fs", "0K"),             # латентность ФС для маленького файла
            ("lat_fs", "10K"),            # латентность ФС для файла 10KB
            ("bw_file_rd", f"65536 open2close {self.test_file}"),  # пропускная способность при чтении файла
        ]

        with open(self.results_file, 'w') as f:
            f.write(f"LMbench Results\n")
            f.write(f"Started: {system.leave_command('date', returncode=True)[0]}\n")
            f.write(f"{'='*60}\n\n")

        for concur in self.concurrency:
            log.info(f"Запуск LMbench с {concur} параллельными потоками")
            for test_name, args in tests:
                log.info(f"Running {test_name} {concur} {args}...")
                output, code = self.run_test(test_name, concur, args)
                key = f"{test_name}_{args}_{concur}"
                status_code_dict[key] = code

                self.writer.wrs(cl=self.__class__,
                                method=self.start_test.__name__,
                                test=f"{test_name} {concur} {args}",
                                status=code)

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
            
            # Результаты с группировкой по concurrency
            results_by_concurrency = {str(concur): {} for concur in self.concurrency}
            test_blocks = re.findall(r'Test: (.+?)\n=+\n(.*?)\n=+', content, re.DOTALL)
            
            for test_block_name, result_text in test_blocks:
                test_block_name = test_block_name.strip()
                
                concur_match = re.search(r'concurrency[=:](\d+)', test_block_name, re.IGNORECASE)
                
                if concur_match:
                    concur = concur_match.group(1)
                    clean_test_name = re.sub(r'\s*\(concurrency[=:]\d+\)', '', test_block_name).strip()
                    if 'bw_file_rd' in clean_test_name:
                        clean_test_name = 'bw_file_rd'
                else:
                    log.warning(f"Не удалось определить concurrency для теста: {test_block_name}")
                    continue
                
                # Парсим значение в зависимости от типа теста
                parsed_value = self._parse_test_value(clean_test_name, result_text)
                
                if parsed_value:
                    results_by_concurrency[concur][clean_test_name] = parsed_value
            
            json_path = f"{RESULTS_MAIN_DIR}/{RESULT_LMBENCH_NAME}"
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(results_by_concurrency, f, indent=4, ensure_ascii=False)
            
            log.info(f"LMbench: результаты сохранены в {json_path}")
            log.info(f"Собрано результатов для {len(results_by_concurrency)} уровней concurrency")
            
            return True, True 
        except Exception as e:
            log.critical(f"LMbench: ошибка при сохранении результатов: {e}")
            import traceback
            log.critical(traceback.format_exc())
            return True, False
            

    def _parse_test_value(self, test_name, result_text):
        """
        Парсит значение теста в зависимости от его типа
        """
        if 'lat_mem_rd' in test_name:
            numbers = re.findall(r'[\d\.]+\s+([\d\.]+)', result_text)
            if numbers:
                return {"value": float(numbers[-1]), "unit": "nanoseconds"}
                
        elif 'lat_ctx' in test_name:
            numbers = re.findall(r'(\d+)\s+([\d\.]+)', result_text)
            if numbers:
                # Берем последнее значение (максимальное количество процессов)
                return {"value": float(numbers[-1][1]), "unit": "microseconds"}
                
        elif 'lat_fs' in test_name:
            # Ищем значение в operations/sec
            match = re.search(r'(\d+)\s+operations/sec', result_text)
            if match:
                return {"value": int(match.group(1)), "unit": "operations/sec"}
            # Альтернативный парсинг для других форматов
            numbers = re.findall(r'(\d+)\s+(\d+)', result_text)
            if numbers:
                return {"value": int(numbers[-1][-1]), "unit": "operations/sec"}
                
        elif 'bw_mem' in test_name:
            numbers = re.findall(r'[\d\.]+\s+([\d\.]+)', result_text)
            if numbers:
                return {"value": float(numbers[-1]), "unit": "MB/s"}
                
        elif 'bw_file_rd' in test_name:
            # Для bw_file_rd ищем число в MB/s
            match = re.search(r'([\d\.]+)\s+MB/s', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "MB/s"}
            # Альтернативный парсинг
            numbers = re.findall(r'([\d\.]+)', result_text)
            if numbers:
                return {"value": float(numbers[-1]), "unit": "MB/s"}
                
        elif 'lat_pipe' in test_name:
            match = re.search(r'([\d\.]+)\s+microseconds', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "microseconds"}
                
        elif 'bw_pipe' in test_name:
            match = re.search(r'([\d\.]+)\s+MB/s', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "MB/s"}
                
        elif 'lat_proc' in test_name:
            match = re.search(r'([\d\.]+)\s+microseconds', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "microseconds"}
                
        elif 'lat_sig' in test_name:
            match = re.search(r'([\d\.]+)\s+microseconds', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "microseconds"}
                
        elif 'lat_syscall' in test_name:
            match = re.search(r'([\d\.]+)\s+microseconds', result_text)
            if match:
                return {"value": float(match.group(1)), "unit": "microseconds"}
        else:
            numbers = re.findall(r'([\d\.]+)', result_text)
            if numbers:
                return {"value": float(numbers[0]), "unit": "unknown"}
        
        return None

