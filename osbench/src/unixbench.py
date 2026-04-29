
from os import chdir, environ

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

        log.info("Запуск UnixBench")
        
        environ['LANG'] = 'en_US.UTF-8'
        environ['LC_ALL'] = 'en_US.UTF-8'
        
        ub_dir = f"{MAIN_DIR}/benchmarks/UnixBench/byte-unixbench/UnixBench/"
        concurrency = [self.low_concurrency] + list(range(self.step, self.high_concurrency, self.step))
        run_cmd_args = ' '.join(f"-c {c}" for c in concurrency)

        chdir(ub_dir)
        system.leave_command("sudo chmod +x Run")
        result, code = system.leave_command(f"./Run {run_cmd_args}")

        if code:
            log.info(result)
            log.info("UnixBench: - тестирование завершено успешно")
            return result, True
        else:
            log.error(result)
            log.error("UnixBench: - тестирование провалено")
            return result, False


    def get_results(self):
        pass
        

        
