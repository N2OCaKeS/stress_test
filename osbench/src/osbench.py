
import sys

from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from unixbench import UnixBench
from fs_mark import FsMark





ub_test = UnixBench()
fs_mark = FsMark()


ub_test.start_test()
ub_test.get_results()

fs_mark.start_test()
fs_mark.get_results()

    
