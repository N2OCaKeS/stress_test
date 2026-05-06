
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
        makedirs(self.results_dir, exist_ok=True)

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
        status_code_list = []

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
            ("bw_mem", "16G cp"),
            
            # IPC
            ("lat_pipe", ""),
            ("bw_pipe", ""),
            ("lat_proc", "fork"),
            ("lat_proc", "exec"),
            
            # Файловая система
            ("lat_fs", "0K"),
            ("lat_fs", "10K"),
            ("bw_file_rd", "65536 64M"),
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

