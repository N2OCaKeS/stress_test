

from os import chdir, mkdir

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
    INODE_COUNT
)


class FsMark(Test):
    """
    FS_MARK
    """
    def __init__(self,
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


    @status_check  
    def start_test(self):

        log.info("Запуск fs_mark")

        """
        Создание тестовой директории
        """
        storage_name = system.leave_command("lsblk | awk 'NR==2' | awk '{print $1;}'")
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


    def get_results(self):
        pass
    