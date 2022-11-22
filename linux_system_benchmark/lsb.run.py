# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: vgusev@astralinux.ru
# ; Date: 2022
# ;===========================================================

import argparse
import subprocess


parser = argparse.ArgumentParser(description="DESCRIPTION")
parser.add_argument('-m', '--mode',
                    action='store',
                    required=False,
                    choices=['default',
                             'extended',],
                    default='default',
                    help='help me',
                    dest='MODE')

def test_run():
    
    #Список тестов
    tests_list = ['dhry2reg', 'whetstone-double', 'syscall', 'pipe', 'context1', 'spawn', 'execl', 'fstime-w', 'fstime-r', 'fstime', 
            'fsbuffer-w', 'fsbuffer-r', 'fsbuffer', 'fsdisk-w ', 'fsdisk-r', 'fsdisk', 'shell1', 'shell8']  
    
    #Количество прогонов тестов
    runs_number = 12    
    #Количество тестов
    tests_number = len(tests_list) - 1  
    test_name = tests_list[tests_number]
    cycles = runs_number

    while tests_number > 0:

        while cycles > 0:

            subprocess.run(['cd byte-unixbench/UnixBench && ./Run -c 6 -i 1 %s' %(test_name)], shell=True)
            cycles = cycles - 1
        
        tests_number = tests_number - 1
        test_name = tests_list[tests_number]
        cycles = runs_number

args = parser.parse_args()

if args.MODE == 'default':

    test_run()
 
elif args.MODE == 'extended':

    print("In developing")
