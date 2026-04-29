
import sys

from config.conf import MAIN_DIR
from src.osb_logger import log
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from unixbench import UnixBench



log.setup(
    name="OSBench",
    log_file=f"{MAIN_DIR}/logs/osbench.log",
    log_level="DEBUG",
    console=True,
    colored_console=True
)



ub_test = UnixBench()





ub_test.start_test()


    
