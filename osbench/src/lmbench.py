
from os import makedirs

from lib import Test, system, status_check
from osb_logger import log
from config.conf import (
    MAIN_DIR
)


class LMBench(Test):
    """
    LMbench
    """
    def __init__(self,
                 report_filename=None):
        
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
        status_code_list = []

        log.info(f"Создание тестового файла: {self.test_file}")
        system.leave_command(f"dd if=/dev/zero of={self.test_file} bs=1M count=100", returncode=True)

        tests = [
            # Ядро
            ("lat_syscall", "null"),
            ("lat_syscall", "read"),
            ("lat_syscall", "write"),
            ("lat_ctx", "-s 0 2 4 8 16 24 32 64 128"),
            ("lat_sig", "inst"),
            ("lat_sig", "hndl"),
            
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
            status_code_list.append(code)

        if all(code for code in status_code_list):
            log.info("LMbench: - тестирование завершено успешно")
            return True, True
        else:
            log.error("LMbench: - тестирование провалено")
            return True, False

        
    @status_check
    def get_results(self):
        """
        Получить результаты и сохранить в JSON
        """

