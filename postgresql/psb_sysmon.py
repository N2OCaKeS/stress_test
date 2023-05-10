import time

from libs.libscanner import Scanner
from libs.libpsb import get_memory_load_by_psql
from psb_conf import DATA_SYSMON_FILENAME

file = open(f'{DATA_SYSMON_FILENAME}', 'w')
file.close()

sc = Scanner()

while True:
    time.sleep(0.25)
    cpu_load = sc.get_cpu_load()
    mem_load = sc.get_memory_load()
    disk_load = sc.get_disk_load()
    mem_psql_load = get_memory_load_by_psql()
    # print(cpu_load, mem_load, mem_psql_load, disk_load)
    file_report_system_load = open(f'{DATA_SYSMON_FILENAME}', 'a+')
    file_report_system_load.write(f'{cpu_load} {mem_load} {mem_psql_load} {disk_load}\n')
    file_report_system_load.close()
    time.sleep(29)