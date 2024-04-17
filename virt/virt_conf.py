import os

REPORT_PATH = f'{os.getcwd()}/test_results'
TEMPLATE_PATH = f'{os.getcwd()}/templates'
INFO_FILENAME = 'virt_info.txt'
VM_INFONAME = 'av.info'
VM_KERNEL = 'kernel.info'
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'


#StealTime
LOW = 1         #TEST_MASHINES --- # count fot middleserver
HIGH = 70       #TEST_MASHINES --- # count fot middleserver
ST_vCPU = 2     #Steal Time vCPU
ST_RAM = 2048   #Steal Time RAM


#Flexible I/O tester
FIOVERS_17x = 'fio_3.12-2_amd64.deb'
FIOVERS_18x = 'fio_3.33-3_amd64.deb'
FIO_vCPU = 16
FIO_RAM = 32768
BLOCK_SIZE = '4k'
FILE_SIZE = '10G'
IO_DEPTH_1 = 1
IO_DEPTH_128 = 128

