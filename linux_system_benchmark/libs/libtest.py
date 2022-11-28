import subprocess
from lsb_conf import TESTS_LIST, NUMBER_OF_CYCLES
import re

class TestSet:

    def test(self):

        number_of_cycles = NUMBER_OF_CYCLES

        for test_name in TESTS_LIST:
            while number_of_cycles > 0:
                cmd_result = subprocess.run('cd byte-unixbench/UnixBench && ./Run -c 6 -i 1 ' + test_name,
                               stderr=subprocess.DEVNULL,
                               stdout=subprocess.PIPE,
                               shell=True)
                number_of_cycles = number_of_cycles - 1

            number_of_cycles = NUMBER_OF_CYCLES