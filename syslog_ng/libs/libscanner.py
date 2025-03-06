# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: ivelikanov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import os
import shutil
import time
from datetime import datetime


class Scanner:

    """
    Конструктор
    """
    def __init__(self):
        self.time_start = None
        self.time_end = None
        self.times = []
    
    """
    Получить загрузку процессора (В том числе по конкретному ядру)
    - Первый аргумент № ядра. По умолчанию 0 - суммарная загрузка
    """
    def get_cpu_load(self, number_kernel=0):
        def cat_proc_cpu(num_kernel):
            if os.cpu_count() < num_kernel:
                print("\033[31mВы пытаетесь узнать загрузку ядра №{kernel}, но существует только {os_count}!\033[0m".format(kernel=num_kernel, os_count=os.cpu_count()))
                raise ValueError
            else:
                with open('/proc/stat', 'r') as procfile:
                    temp_str = []
                    for _ in range(num_kernel + 1):
                        if num_kernel == 0:
                            temp_str = procfile.readline().split(' ')[2:-1]
                        else:
                            temp_str = procfile.readline().split(' ')[1:-1]
                    usertime, systime, idle = temp_str[0], temp_str[2], temp_str[3]
                    return int(usertime), int(systime), int(idle)

        usertime1, systime1, idle1 = cat_proc_cpu(number_kernel)
        time.sleep(0.75)
        usertime2, systime2, idle2 = cat_proc_cpu(number_kernel)
        usert_and_syst1 = usertime1 + systime1
        total_t1 = usert_and_syst1 + idle1
        usert_and_syst2 = usertime2 + systime2
        total_t2 = usert_and_syst2 + idle2
        summ_load_cpu = (usert_and_syst2 - usert_and_syst1) * 100 / (total_t2 - total_t1)
        return int(summ_load_cpu)

    """
    Получить загрузку оперативной памяти
    """
    def get_memory_load(self):
        with open('/proc/meminfo', 'r') as procfile:  
            mem = []      
            for line in procfile.readlines():
                if "MemTotal" in line or "MemAvailable" in line:
                    mem.append(int(line[:-3].replace(" ", "").split(":")[1]))
                
            # mem[0] - MemTotal in /proc/meminfo
            # mem[1] - Mem MemAvailable in /proc/meminfo

            mem_used = mem[0] - mem[1]
            load_mem = round((mem_used / mem[0]) * 100)
            return load_mem

    """
    Получить нагрузку на диск (дисковое заполнение)
    """
    def get_disk_load(self):
        total, used, free = shutil.disk_usage("/")
        return round((used/total) * 100)

    """
    Засечь время
    """
    def start_the_time(self):
        self.time_start = datetime.now()
        return self.time_start

    """
    Остановить время
    """
    def stop_the_time(self):
        self.time_end = datetime.now() - self.time_start
        self.times.append(self.time_end)
        return self.time_end