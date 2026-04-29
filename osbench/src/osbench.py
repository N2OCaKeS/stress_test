
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from unixbench import UnixBench





ub_test = UnixBench()

ub_test.start_test()


    
