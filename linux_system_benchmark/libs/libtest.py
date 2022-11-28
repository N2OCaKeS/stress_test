import subprocess
from lsb_conf import TESTS_LIST, NUMBER_OF_CYCLES
import re


class Test():
    def test1(self):
        for test_name in TESTS_LIST:
            for _ in range(NUMBER_OF_CYCLES):
                cmd_result = subprocess.run('cd byte-unixbench/UnixBench && ./Run -c 6 -i 1 ' + test_name,
                               stderr=subprocess.DEVNULL,
                               stdout=subprocess.PIPE,
                               shell=True)



class TestSet():
    def test_set1(self):
        Test.test1()

