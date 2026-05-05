import re
import json
import pandas as pd

from os import chdir, mkdir, makedirs, path

from lib import Test, system, status_check
from osb_logger import log
from config.conf import (
    FILE_SIZE,
    FILES,
    FILES_LIMIT,
    FILES_STEP,
    MAIN_DIR,
    FS,
    STORAGE_MOUNT_DIR,
    INODE_COUNT,
    RESULTS_MAIN_DIR,
    RESULT_FSMARK_NAME
)



class FsMarkParser:
    def __init__(self, 
                 report_filename):
        
        self.report_filename = report_filename
        
    
    def parse_results(self):
        """
        Парсит результаты fs_mark из файла
        """
        if not path.exists(self.report_filename):
            log.warning(f"Файл {self.report_filename} не найден")
            return pd.DataFrame()
    
        with open(self.report_filename, 'r') as report_file:
            all_lines = report_file.readlines()
            
            # Собираем все строки результатов (те, которые начинаются с пробелов и цифр)
            result_lines = []
            for line in all_lines:
                # Пропускаем пустые строки и заголовки
                if line.strip() and not line.startswith('FSUse%') and not line.startswith('#') and not line.startswith('##'):
                    # Проверяем, что строка начинается с цифры или пробела и цифры
                    if re.match(r'\s*\d+', line):
                        result_lines.append(line.strip())
        
        all_values = []
        for line in result_lines:
            values = line.split()
            all_values.extend(values)
        
        # Проверяем, что количество значений кратно 23
        if len(all_values) % 23 != 0:
            log.warning(f"Warning: Expected multiples of 23 values, got {len(all_values)}")
            # Обрезаем до ближайшего кратного 23
            all_values = all_values[:-(len(all_values) % 23)]
        
        # Создаем списки для каждой метрики (каждый 23-й элемент)
        indices = list(range(0, len(all_values), 23))
        
        self.fs_use_lst = [int(all_values[i]) for i in indices] 
        self.file_count_lst = [int(all_values[i+1]) for i in indices] 
        self.file_size_lst = [int(all_values[i+2]) for i in indices] 
        self.speed_lst = [float(all_values[i+3]) for i in indices] 
        self.app_overhead_lst = [int(all_values[i+4]) for i in indices] 
        
        self.create_min_lst = [int(all_values[i+5]) for i in indices] 
        self.create_avg_lst = [int(all_values[i+6]) for i in indices] 
        self.create_max_lst = [int(all_values[i+7]) for i in indices] 
        
        self.write_min_lst = [int(all_values[i+8]) for i in indices] 
        self.write_avg_lst = [int(all_values[i+9]) for i in indices] 
        self.write_max_lst = [int(all_values[i+10]) for i in indices]  
        
        self.fsync_min_lst = [int(all_values[i+11]) for i in indices]  
        self.fsync_avg_lst = [int(all_values[i+12]) for i in indices]  
        self.fsync_max_lst = [int(all_values[i+13]) for i in indices]  
        
        self.sync_min_lst = [int(all_values[i+14]) for i in indices]  
        self.sync_avg_lst = [int(all_values[i+15]) for i in indices]  
        self.sync_max_lst = [int(all_values[i+16]) for i in indices]  
        
        self.close_min_lst = [int(all_values[i+17]) for i in indices]  
        self.close_avg_lst = [int(all_values[i+18]) for i in indices]  
        self.close_max_lst = [int(all_values[i+19]) for i in indices]  
        
        self.unlink_min_lst = [int(all_values[i+20]) for i in indices]  
        self.unlink_avg_lst = [int(all_values[i+21]) for i in indices]  
        self.unlink_max_lst = [int(all_values[i+22]) for i in indices]  
        
        # Создаем DataFrame
        self.raw_table = pd.DataFrame({
            'fs_use': self.fs_use_lst,
            'file_count': self.file_count_lst,
            'file_size': self.file_size_lst,
            'speed': self.speed_lst,
            'app_overhead': self.app_overhead_lst,
            'create_min': self.create_min_lst,
            'create_avg': self.create_avg_lst,
            'create_max': self.create_max_lst,
            'write_min': self.write_min_lst,
            'write_avg': self.write_avg_lst,
            'write_max': self.write_max_lst,
            'fsync_min': self.fsync_min_lst,
            'fsync_avg': self.fsync_avg_lst,
            'fsync_max': self.fsync_max_lst,
            'sync_min': self.sync_min_lst,
            'sync_avg': self.sync_avg_lst,
            'sync_max': self.sync_max_lst,
            'close_min': self.close_min_lst,
            'close_avg': self.close_avg_lst,
            'close_max': self.close_max_lst,
            'unlink_min': self.unlink_min_lst,
            'unlink_avg': self.unlink_avg_lst,
            'unlink_max': self.unlink_max_lst
        })

        return self.raw_table
       


class FsMark(Test, FsMarkParser):
    """
    FS_MARK
    """
    def __init__(self,
                 report_filename=None,
                 f_size=FILE_SIZE,
                 f_count=FILES,
                 f_step=FILES_STEP,
                 f_limit=FILES_LIMIT,
                 fs=FS):
        
        self.f_size = f_size
        self.f_count = f_count
        self.f_step = f_step
        self.f_limit = f_limit
        self.fs = fs
        self.test_dir = STORAGE_MOUNT_DIR
        self.t_dir1 = "test1"
        self.t_dir2 = "test2"
        self.t_dir3 = "test3"

        if report_filename is None:
            self._report_filename = f"{MAIN_DIR}/benchmarks/fs_mark/fs_log.txt"
        else:
            self._report_filename = report_filename

        FsMarkParser.__init__(self, self._report_filename)


    @status_check  
    def start_test(self):

        log.info("Запуск fs_mark")

        """
        Создание тестовой директории
        """
        storage_name = system.command("lsblk | awk 'NR==2' | awk '{print $1;}'")
        log.debug(storage_name)

        if system.command(f"lsblk | grep {storage_name}", returncode=True) == 0:
            if system.command(f"lsblk | grep {storage_name}1", returncode=True) == 0:
                system.command(f"umount {STORAGE_MOUNT_DIR}", returncode=True)
                system.command(f"sudo parted -s /dev/{storage_name} select && sudo parted -s /dev/{storage_name} rm 1", returncode=True)

        if self.fs == "xfs":
            system.leave_command(f"sudo parted -s /dev/{storage_name} mklabel gpt mkpart primary xfs 0% 100%")
            system.leave_command(f"sudo mkfs -t {self.fs} -f /dev/{storage_name}1")
        else:
            system.leave_command(f"sudo parted -s /dev/{storage_name} mklabel gpt mkpart primary {self.fs} 0% 100%")
            system.leave_command(f"sudo mkfs -t {self.fs} {INODE_COUNT} -F /dev/{storage_name}1")

        system.leave_command(f"mount /dev/{storage_name}1 {STORAGE_MOUNT_DIR}")

        mkdir(f"{STORAGE_MOUNT_DIR}/{self.t_dir1}", mode=0o755)
        mkdir(f"{STORAGE_MOUNT_DIR}/{self.t_dir2}", mode=0o755)
        mkdir(f"{STORAGE_MOUNT_DIR}/{self.t_dir3}", mode=0o755)


        """
        Запуск теста
        """
        status = []
        fs_mark_dir = f"{MAIN_DIR}/benchmarks/fs_mark"
        run_bench = "./fs_mark -t 24 -d {test_dir1} -d {test_dir2} -d {test_dir3} -s {f_size} -n {f_count} -v"
        files_count_list = list(range(self.f_count, self.f_limit, self.f_step))

        chdir(fs_mark_dir)
        system.leave_command("sudo chmod +x fs_mark", returncode=True)

        for count in files_count_list:
            result, code = system.leave_command(run_bench.format(
                test_dir1=f"{STORAGE_MOUNT_DIR}/{self.t_dir1}",
                test_dir2=f"{STORAGE_MOUNT_DIR}/{self.t_dir2}",
                test_dir3=f"{STORAGE_MOUNT_DIR}/{self.t_dir3}",
                f_size=self.f_size,
                f_count=count
            ), returncode=True)
            status.append(code)

        log.debug(f"Codes status: {status}")

        if all(code for code in status):
            log.info("fs_mark: - тестирование завершено успешно")
            return True, True
        else:
            log.error("fs_mark: - тестирование провалено")
            return True, False


    @status_check
    def get_results(self):
        """
        Получить результаты и сохранить в JSON
        """
        results = self.parse_results()
        if results:
            log.debug("DataFrame с результатами:")
            log.debug(results)

            # Создаём директорию, если её нет
            makedirs(RESULTS_MAIN_DIR, exist_ok=True)
            
            # Сохраняем в JSON
            json_path = f"{RESULTS_MAIN_DIR}/{RESULT_FSMARK_NAME}"
            results.to_json(json_path, indent=4, force_ascii=False)
            
            log.info(f"fs_mark: - Результаты сохранены в {json_path}")
            return True, True
        else:
            log.error("fs_mark: - Не удалось получить результаты")
            return True, False

