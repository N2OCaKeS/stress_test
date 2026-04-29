
from os import chdir

from lib import Test, system, status_check
from osb_logger import log
from config.conf import (
    LOW_CONC,
    HIGH_CONC,
    STEP,
    MAIN_DIR
)


class UnixBench(Test):

    def __init__(self,
                 low_concurrency=LOW_CONC,
                 high_concurrency=HIGH_CONC,
                 step=STEP):
        
        self.low_concurrency = low_concurrency
        self.high_concurrency = high_concurrency
        self.step = step


    @status_check  
    def start_test(self):

        def _clean_output(output: str) -> str:
            """
            Удаляет из вывода строки с ошибками локали
            """
            if not output:
                return output
            lines = output.splitlines()
            filtered = [
                line for line in lines 
                if not any(x in line for x in ('locale', 'LC_', 'Cannot set', 'Wide character'))
                ]
            return '\n'.join(filtered)

        log.info("Запуск UnixBench")
        ub_dir = f"{MAIN_DIR}/benchmarks/UnixBench/byte-unixbench/UnixBench/"
        concurrency = [self.low_concurrency] + list(range(self.step, self.high_concurrency, self.step))
        run_cmd_args = ' '.join(f"-c {c}" for c in concurrency)

        chdir(ub_dir)
        system.leave_command("sudo chmod +x Run", returncode=True)
        result, code = system.leave_command(f"./Run {run_cmd_args}", returncode=True)
        clean_result = _clean_output(result)

        log.debug(f"code = {code}, type = {type(code)}")

        if code:
            log.info(clean_result)
            log.info("UnixBench: - тестирование завершено успешно")
            return result, True
        else:
            log.error(clean_result)
            log.error("UnixBench: - тестирование провалено")
            return result, False


    def get_results(self):
        pass
        

        
