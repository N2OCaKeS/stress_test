import re
import json
import pandas as pd

from os import chdir, mkdir, makedirs, path

from lib import Test, system, status_check, Writer
from osb_logger import log, Colors
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
    RESULT_FSMARK_NAME,
    RESULTS_STATUS,
    DEFAULT_DISK
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
       


class FSMark(Test, FsMarkParser):
    """
    FS_MARK
    """
    def __init__(self,
                 report_filename=None,
                 f_size=FILE_SIZE,
                 f_count=FILES,
                 f_step=FILES_STEP,
                 f_limit=FILES_LIMIT,
                 fs=FS,
                 dd=DEFAULT_DISK):
        
        self.test_success = False
        self.f_size = f_size
        self.f_count = f_count
        self.f_step = f_step
        self.f_limit = f_limit
        self.fs = fs
        self.dd = dd
        self.test_dir = STORAGE_MOUNT_DIR
        self.t_dir1 = "test1"
        self.t_dir2 = "test2"
        self.t_dir3 = "test3"
        self.writer = Writer(file_name=RESULTS_STATUS)

        if report_filename is None:
            self._report_filename = f"{MAIN_DIR}/benchmarks/fs_mark/fs_log.txt"
        else:
            self._report_filename = report_filename

        FsMarkParser.__init__(self, self._report_filename)


    #@status_check  
    def start_test(self):

        log.info("Запуск fs_mark")

        """
        Создание тестовой директории
        """
        def _is_system_disk(disk_name):
            """
            Проверяет, не является ли диск системным
            """
            # Проверяем, смонтирован ли диск как /
            mounts = system.command(f"mount | grep '/dev/{disk_name}'")
            if '/' in mounts and 'type' in mounts:
                return True
            
            # Проверяем, является ли диск boot-диском
            if system.command(f"lsblk -o MOUNTPOINT /dev/{disk_name} | grep -E '^/boot'", returncode=True) == 0:
                return True
            
            return False
        
        log.warning(f"⚠️ ВНИМАНИЕ! В процессе тестирования данные на диске '{self.dd}' могут быть уничтожены!")
        storage_name = self.dd
        if storage_name not in system.command(f"lsblk | grep {storage_name}"):
            storage_name = system.command("lsblk | awk 'NR==2' | awk '{print $1;}'")
        log.debug(storage_name)

        if not _is_system_disk(storage_name):
            if system.command(f"lsblk | grep {storage_name}", returncode=True) == 0:
                if system.command(f"lsblk | grep {storage_name}1", returncode=True) == 0:
                    system.command(f"umount {STORAGE_MOUNT_DIR}", returncode=True)
                    system.command(f"sudo parted -s /dev/{storage_name} select && sudo parted -s /dev/{storage_name} rm 1", returncode=True)

            if self.fs == "xfs":
                system.leave_command(f"sudo parted -s /dev/{storage_name} mklabel gpt mkpart primary xfs 0% 100%", console=False)
                system.leave_command(f"sudo mkfs -t {self.fs} -f /dev/{storage_name}1", console=False)
            else:
                system.leave_command(f"sudo parted -s /dev/{storage_name} mklabel gpt mkpart primary {self.fs} 0% 100%", console=False)
                system.leave_command(f"sudo mkfs -t {self.fs} {INODE_COUNT} -F /dev/{storage_name}1", console=False)

            system.leave_command(f"mount /dev/{storage_name}1 {STORAGE_MOUNT_DIR}", console=False)

            mkdir(f"{STORAGE_MOUNT_DIR}/{self.t_dir1}", mode=0o755)
            mkdir(f"{STORAGE_MOUNT_DIR}/{self.t_dir2}", mode=0o755)
            mkdir(f"{STORAGE_MOUNT_DIR}/{self.t_dir3}", mode=0o755)
        else:
            log.error(f"Диск {storage_name} является системным! Тест остановлен.")
            return False, False


        """
        Запуск теста
        """
        status = []
        fs_mark_dir = f"{MAIN_DIR}/benchmarks/fs_mark"
        run_bench = "./fs_mark -t 24 -d {test_dir1} -d {test_dir2} -d {test_dir3} -s {f_size} -n {f_count} -v"
        files_count_list = list(range(self.f_count, self.f_limit, self.f_step))

        chdir(fs_mark_dir)
        system.leave_command("sudo chmod +x fs_mark", returncode=True, console=False)

        for count in files_count_list:
            result, code = system.leave_command(run_bench.format(
                test_dir1=f"{STORAGE_MOUNT_DIR}/{self.t_dir1}",
                test_dir2=f"{STORAGE_MOUNT_DIR}/{self.t_dir2}",
                test_dir3=f"{STORAGE_MOUNT_DIR}/{self.t_dir3}",
                f_size=self.f_size,
                f_count=count
            ), returncode=True, console=False)
            status.append(code)

            self.writer.wrs(
                cl=self.__class__,
                method=self.start_test.__name__,
                test=f"fs_mark -n {count} -s {self.f_size}",
                status=code,
                message=f"Files: {count}, Size: {self.f_size}KB"
            )

        system.leave_command(f"umount {STORAGE_MOUNT_DIR}", console=False)

        log.debug(f"Codes status: {status}")

        if all(code for code in status):
            log.debug("fs_mark: - тестирование завершено успешно")
            log.info(f"{Colors.GREEN}Все тесты успешно пройдены: {status}{Colors.RESET}")
            self.test_success = True
            return True, True
        else:
            log.critical("fs_mark: - тестирование провалено")
            log.error(f"{Colors.RED}Статусы: {status}{Colors.RESET}")
            self.test_success = False
            return True, False


    #@status_check
    def get_results(self):
        """
        Получить результаты и сохранить в JSON
        """
        if not self.test_success:
            log.critical(f"{Colors.RED}fs_mark: тесты не были успешно завершены, сбор результатов пропущен{Colors.RESET}")
            return True, False
        
        results = self.parse_results()
        if results is not None and not results.empty:
            log.debug("DataFrame с результатами:")
            log.debug(results)

            # Создаём директорию, если её нет
            makedirs(RESULTS_MAIN_DIR, exist_ok=True)
            
            # Сохраняем в JSON
            json_path = f"{RESULTS_MAIN_DIR}/{RESULT_FSMARK_NAME}"
            results.to_json(json_path, indent=4, force_ascii=False)
            
            log.debug(f"fs_mark: - Результаты сохранены в {json_path}")
            return True, True
        else:
            log.error("fs_mark: - Не удалось получить результаты")
            return True, False

