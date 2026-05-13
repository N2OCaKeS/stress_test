import re
import json

from os import chdir, makedirs
from pathlib import Path

from lib import Test, system, status_check, Writer
from osb_logger import log, Colors
from config.conf import (
    MAIN_DIR,
    RESULTS_MAIN_DIR,
    RESULT_PERF_BENCH_NAME,
    RESULTS_STATUS
)


class PerfBenchParser:
    """
    Парсер результатов perf bench
    """
    
    def __init__(self):
        self.results = {}
    
    def parse_sched_output(self, output: str, test_name: str) -> dict:
        """
        Парсинг результатов sched тестов
        """
        result = {"test": test_name, "value": None, "unit": "seconds"}
        
        # Ищем время выполнения
        # Average of 3 runs: 0.123 seconds
        match = re.search(r'Average.*?([\d\.]+)\s+seconds', output)
        if not match:
            # Альтернативный формат: "Runtime: 0.123 seconds"
            match = re.search(r'Runtime:\s*([\d\.]+)\s+seconds', output)
        if not match:
            # Число в конце строки с секундами
            match = re.search(r'([\d\.]+)\s+seconds', output)
        
        if match:
            result["value"] = float(match.group(1))
        else:
            log.warning(f"Не удалось распарсить {test_name}: {output[:100]}")
        
        return result
    
    def parse_mem_output(self, output: str, test_name: str) -> dict:
        """
        Парсинг результатов mem тестов
        """
        result = {"test": test_name, "value": None, "unit": "MB/sec"}
        
        # Ищем пропускную способность
        # Пример: "74688.000 MB/sec"
        match = re.search(r'([\d\.]+)\s+MB/sec', output)
        if match:
            result["value"] = float(match.group(1))
        else:
            log.warning(f"Не удалось распарсить {test_name}: {output[:100]}")
        
        return result
    
    def parse_futex_output(self, output: str, test_name: str) -> dict:
        """
        Парсинг результатов futex тестов
        """
        result = {"test": test_name, "value": None, "unit": "ops/sec"}
        
        # Пример: "Average ops/sec: 1234567.89"
        match = re.search(r'Average ops/sec:\s*([\d\.]+)', output)
        if not match:
            # Альтернативный формат: "1234567.89 ops/sec"
            match = re.search(r'([\d\.]+)\s+ops/sec', output)
        
        if match:
            result["value"] = float(match.group(1))
        else:
            log.warning(f"Не удалось распарсить {test_name}: {output[:100]}")
        
        return result
    
    def parse_epoll_output(self, output: str, test_name: str) -> dict:
        """
        Парсинг результатов epoll тестов
        """
        result = {"test": test_name, "value": None, "unit": "ops/sec"}
        
        # Пример: "Average ops/sec: 1234567.89"
        match = re.search(r'Average ops/sec:\s*([\d\.]+)', output)
        if not match:
            match = re.search(r'([\d\.]+)\s+ops/sec', output)
        
        if match:
            result["value"] = float(match.group(1))
        else:
            log.warning(f"Не удалось распарсить {test_name}: {output[:100]}")
        
        return result
    
    def parse(self, test_name: str, output: str) -> dict:
        """
        Основной метод парсинга в зависимости от типа теста
        """
        if 'sched' in test_name:
            return self.parse_sched_output(output, test_name)
        elif 'mem' in test_name:
            return self.parse_mem_output(output, test_name)
        elif 'futex' in test_name:
            return self.parse_futex_output(output, test_name)
        elif 'epoll' in test_name:
            return self.parse_epoll_output(output, test_name)
        else:
            return {"test": test_name, "value": None, "unit": "unknown"}
    
    def add_result(self, test_name: str, result: dict):
        """
        Добавление результата в общую структуру
        """
        self.results[test_name] = result


class PerfBench(Test):
    """
    Perf Bench - тестирование производительности ядра
    Тесты:
        - sched: планировщик и IPC
        - mem: производительность памяти
        - futex: быстрые блокировки
        - epoll: опрос событий
    """
    
    def __init__(self):
        self.test_success = False
        self.perf_dir = f"{MAIN_DIR}/benchmarks/perf bench"
        self.results_file = f"{self.perf_dir}/perf_bench_result.txt"
        self.writer = Writer(file_name=RESULTS_STATUS)
        self.parser = PerfBenchParser()
        
        makedirs(self.perf_dir, exist_ok=True)
    
    def run_perf_test(self, test_args: str) -> tuple:
        """
        Запуск отдельного теста perf bench
        
        Args:
            test_args: аргументы для perf bench (например, "sched pipe")
        
        Returns:
            (output, success): вывод команды и статус успеха
        """
        cmd = f"perf bench {test_args}"
        result, code = system.leave_command(cmd, returncode=True)
        return result, code
    
    @status_check
    def start_test(self):
        """
        Запуск всех тестов perf bench
        """
        log.info("Запуск Perf Bench")
        
        tests = [
            # Sched тесты (планировщик и IPC)
            ("sched pipe", "sched pipe", "sched"),
            ("sched messaging", "sched messaging", "sched"),
            
            # Mem тесты (производительность памяти)
            ("memcpy", "mem memcpy", "mem"),
            ("memcpy 1M", "mem memcpy -l 1M", "mem"),
            ("memcpy 10M", "mem memcpy -l 10M", "mem"),
            
            # Futex тесты (быстрые блокировки)
            ("futex hash", "futex hash", "futex"),
            ("futex wake", "futex wake", "futex"),
            ("futex requeue", "futex requeue", "futex"),
            
            # Epoll тесты (опрос событий)
            ("epoll wait", "epoll wait", "epoll"),
            ("epoll ctl", "epoll ctl", "epoll"),
        ]
        
        status_codes = []
        
        # Очищаем файл результатов
        with open(self.results_file, 'w') as f:
            f.write("Perf Bench Results\n")
            f.write(f"Started: {system.leave_command('date', returncode=True)[0]}\n")
            f.write(f"{'='*60}\n\n")
        
        for display_name, test_args, test_type in tests:
            log.info(f"Running: {display_name}...")
            
            output, code = self.run_perf_test(test_args)
            status_codes.append(code)
            
            # Сохраняем вывод в файл
            with open(self.results_file, 'a') as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Test: {display_name}\n")
                f.write(f"Command: perf bench {test_args}\n")
                f.write(f"{'='*60}\n")
                f.write(output)
                f.write(f"\n{'='*60}\n\n")
            
            # Парсим результат
            parsed = self.parser.parse(display_name, output)
            self.parser.add_result(display_name, parsed)
            
            if parsed["value"]:
                log.debug(f"  Result: {parsed['value']} {parsed['unit']}")
            
            self.writer.wrs(
                cl=self.__class__,
                method=self.start_test.__name__,
                test=display_name,
                status=code,
                message=f"Result: {parsed['value']} {parsed['unit']}" if parsed['value'] else ""
            )
        
        all_success = all(status_codes)
        
        self.writer.wrs(
            cl=self.__class__,
            method=self.start_test.__name__,
            test="ALL_TESTS",
            status=all_success,
            message=f"Passed: {sum(status_codes)}/{len(status_codes)} tests"
        )
        
        if all_success:
            log.info("Perf Bench: - тестирование завершено успешно")
            log.debug(f"{Colors.GREEN}Все тесты успешно пройдены{Colors.RESET}")
            self.test_success = True
            return True, True
        else:
            failed = len([c for c in status_codes if not c])
            log.critical(f"Perf Bench: - тестирование провалено (неудачно: {failed}/{len(status_codes)})")
            log.debug(f"{Colors.RED}Некоторые тесты провалены{Colors.RESET}")
            self.test_success = False
            return True, False
    
    @status_check
    def get_results(self):
        """
        Получить результаты и сохранить в JSON
        """
        if not self.test_success:
            log.critical(f"{Colors.RED}Perf Bench: тесты не были успешно завершены, сбор результатов пропущен{Colors.RESET}")
            return True, False
        
        log.info("Сохранение результатов Perf Bench")
        
        # Формируем структуру результатов
        results = self.parser.results
        
        # Сохраняем в JSON
        makedirs(RESULTS_MAIN_DIR, exist_ok=True)
        json_path = f"{RESULTS_MAIN_DIR}/{RESULT_PERF_BENCH_NAME}"
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=4, ensure_ascii=False)
        
        log.info(f"Perf Bench: - результаты сохранены в {json_path}")
        log.info(f"Собрано результатов: {len(results)}")
        
        log.info("=" * 50)
        log.info("КРАТКАЯ СВОДКА РЕЗУЛЬТАТОВ PERF BENCH")
        log.info("=" * 50)
        for test_name, data in results.items():
            if data["value"]:
                log.info(f"  {test_name:20}: {data['value']:>12.2f} {data['unit']}")
        
        self.writer.wrs(
            cl=self.__class__,
            method=self.get_results.__name__,
            test="parse_and_save",
            status=True,
            message=f"Сохранено результатов: {len(results)}"
        )
        
        return True, True
    
