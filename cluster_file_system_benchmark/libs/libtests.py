# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import logging
import subprocess
import concurrent.futures

from time import time
from pathlib import Path
from threading import Lock
from os import listdir, linesep
from cfs_conf import STORAGE_MOUNT_DIR, LOG_PATH, SCRIPT_DIR, REPORT_PATH, REPORT_FILENAME
from libs.libactions import create_file, del_file, \
    create_symlink, del_symlink, \
    create_hardlink, del_hardlink, \
    create_arch, del_arch, \
    create_iso, del_iso, \
    create_big_file, \
    create_big_arch, \
    create_big_iso

logging.basicConfig(filename=LOG_PATH,
                    filemode="a+",
                    level=logging.INFO,
                    format='%(levelname)s: t:%(created)f th:%(thread)d ps:%(process)d <%(name)s> | %(message)s')
log = logging.getLogger()


class Test:

    @staticmethod
    def file_filling(count=10000, start=10, end=110, step=10, mount_dir=STORAGE_MOUNT_DIR):
        print("# TEST # <{}>:".format(Test.file_filling.__name__), end=' ')
        result_output = ''

        for percent in range(start, end, step):
            # Заполнить диск файлами на "percent" процентов
            counter = 0
            create_file(count, percent)

            # Читаем из каждого файла
            for file in listdir('{}/files'.format(mount_dir)):
                with open('{dir}/files/{f}'.format(dir=mount_dir, f=file), 'r') as f:
                    f.read()
                    counter += 1

            if counter == count:
                result_output += "{}% | ".format(percent)
                log.info("{}% | ".format(percent))
            else:
                print(result_output + '\033[91mFAIL\033[0m')
                return False

            # Удаляем файлы c диска
            del_file()

        print(result_output + '\033[92mPASS\033[0m')
        log.info('pass')
        return True

    @staticmethod
    def symlink_filling(count=10000, start=10, end=100, step=5, mount_dir=STORAGE_MOUNT_DIR):
        print("# TEST # <{}>:".format(Test.symlink_filling.__name__), end=' ')
        result_output = ''

        for percent in range(start, end, step):
            # Заполнить диск гибкими ссылками на "percent" процентов
            counter = 0
            create_symlink(count, percent)

            # Читаем из каждого файла
            for file in Path('{}/symlinks'.format(mount_dir)).glob('symlink_*'):
                with open(file, 'r') as f:
                    f.read()
                    counter += 1

            if counter == count:
                result_output += "{}% | ".format(percent)
                log.info("{}% | ".format(percent))
            else:
                print(result_output + '\033[91mFAIL\033[0m')
                return False

            # Удаляем файлы c диска
            del_symlink()

        print(result_output + '\033[92mPASS\033[0m')
        log.info('pass')
        return True

    @staticmethod
    def hardlink_filling(count=10000, start=10, end=100, step=5, mount_dir=STORAGE_MOUNT_DIR):
        print("# TEST # <{}>:".format(Test.hardlink_filling.__name__), end=' ')
        result_output = ''

        for percent in range(start, end, step):
            # Заполнить диск гибкими ссылками на "percent" процентов
            counter = 0
            create_hardlink(count, percent)

            # Читаем из каждого файла
            for file in Path('{}/hardlinks'.format(mount_dir)).glob('hardlink_*'):
                with open(file, 'r') as f:
                    f.read()
                    counter += 1

            if counter == count:
                result_output += "{}% | ".format(percent)
                log.info("{}% | ".format(percent))
            else:
                print(result_output + '\033[91mFAIL\033[0m')
                return False

            # Удаляем файлы c диска
            del_hardlink()

        print(result_output + '\033[92mPASS\033[0m')
        log.info('pass')
        return True

    @staticmethod
    def arch_filling(count=10000, start=10, end=100, step=5, mount_dir=STORAGE_MOUNT_DIR):
        print("# TEST # <{}>:".format(Test.arch_filling.__name__), end=' ')
        result_output = ''

        for percent in range(start, end, step):
            # Заполнить диск архивами на "percent" процентов
            counter = 0
            create_arch(count, percent)

            # Читаем из каждого файла
            for file in listdir('{}/archs'.format(mount_dir)):
                if subprocess.run('tar -xf {dir}/archs/{file}'.format(dir=mount_dir, file=file),
                                  shell=True,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0:
                    counter += 1

            if counter == count:
                result_output += "{}% | ".format(percent)
                log.info("{}% | ".format(percent))
            else:
                print(result_output + '\033[91mFAIL\033[0m')
                return False

            # Удаляем файлы c диска
            del_arch()

        print(result_output + '\033[92mPASS\033[0m')
        log.info('pass')
        return True

    @staticmethod
    def iso_filling(count=10000, start=10, end=100, step=5, mount_dir=STORAGE_MOUNT_DIR):
        print("# TEST # <{}>:".format(Test.iso_filling.__name__), end=' ')
        result_output = ''

        for percent in range(start, end, step):
            # Заполнить диск iso файлами на "percent" процентов
            counter = 0
            create_iso(count, percent)

            # Читаем из каждого файла
            for file in listdir('{}/isos'.format(mount_dir)):
                if subprocess.run('7z x {dir}/isos/{file}'.format(dir=mount_dir, file=file),
                                  shell=True,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0:
                    counter += 1

            if counter == count:
                result_output += "{}% | ".format(percent)
                log.info("{}% | ".format(percent))
            else:
                print(result_output + '\033[91mFAIL\033[0m')
                return False

            # Удаляем файлы c диска
            del_iso()

        print(result_output + '\033[92mPASS\033[0m')
        log.info('pass')
        return True

    @staticmethod
    def big_file_copying(start=10, end=100, step=5, mount_dir=STORAGE_MOUNT_DIR):
        print("# TEST # <{}>:".format(Test.big_file_copying.__name__), end=' ')
        result_output = ''

        for percent in range(start, end, step):
            create_big_file(percent)
            is_identically = False
            is_readed = False
            # скопировать
            if subprocess.run('cp {dir}/big_files/big_file {dir}/big_files/big_file_copy'.format(dir=mount_dir),
                              shell=True,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0:

                # проверить идентичность
                if subprocess.run('diff {dir}/big_files/big_file {dir}/big_files/big_file_copy'.format(dir=mount_dir),
                                  shell=True,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0:
                    is_identically = True

                # проверить читаемость
                with open('{dir}/big_files/big_file_copy'.format(dir=mount_dir), 'r') as f:
                    f.read()
                    is_readed = True

            if is_readed and is_identically:
                result_output += "{}% | ".format(percent)
                log.info("{}% | ".format(percent))
            else:
                print(result_output + '\033[91mFAIL\033[0m')
                return False

            # Удаляем файлы c диска
            del_file()

        print(result_output + '\033[92mPASS\033[0m')
        log.info('pass')
        return True

    @staticmethod
    def big_arch_copying(start=10, end=100, step=5, mount_dir=STORAGE_MOUNT_DIR):
        print("# TEST # <{}>:".format(Test.big_arch_copying.__name__), end=' ')
        result_output = ''

        for percent in range(start, end, step):
            create_big_arch(percent)
            is_identically = False
            is_readed = False
            # скопировать
            if subprocess.run('cp {dir}/big_archs/big_arch.tar {dir}/big_archs/big_arch_copy.tar'.format(dir=mount_dir),
                              shell=True,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0:

                # проверить идентичность
                if subprocess.run('diff {dir}/big_archs/big_arch.tar {dir}/big_archs/big_arch_copy.tar'.format(dir=mount_dir),
                                  shell=True,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0:
                    is_identically = True

                # проверить читаемость
                if subprocess.run('tar -xf {dir}/big_archs/big_arch_copy.tar'.format(dir=mount_dir),
                                  shell=True,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0:
                    is_readed = True

            if is_readed and is_identically:
                result_output += "{}% - arch_size | ".format(percent)
                log.info("{}% - arch_size | ".format(percent))
            else:
                print(result_output + '\033[91mFAIL\033[0m')
                return False

            del_arch()

        print(result_output + '\033[92mPASS\033[0m')
        log.info('pass')
        return True

    @staticmethod
    def big_iso_copying(start=10, end=100, step=5, mount_dir=STORAGE_MOUNT_DIR):
        print("# TEST # <{}>:".format(Test.big_iso_copying.__name__), end=' ')
        result_output = ''

        for percent in range(start, end, step):
            create_big_iso(percent)
            is_identically = False
            is_readed = False
            # скопировать
            if subprocess.run('cp {dir}/big_isos/big_iso.iso {dir}/big_isos/big_iso_copy.iso'.format(dir=mount_dir),
                              shell=True,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0:

                # проверить идентичность
                if subprocess.run('diff {dir}/big_isos/big_iso.iso {dir}/big_isos/big_iso_copy.iso'.format(dir=mount_dir),
                                  shell=True,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0:
                    is_identically = True

                # проверить читаемость
                if subprocess.run('7z x {dir}/big_isos/big_iso_copy.iso'.format(dir=mount_dir),
                                  shell=True,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL).returncode == 0:
                    is_readed = True

            if is_readed and is_identically:
                result_output += "{}% - iso_size | ".format(percent)
                log.info("{}% - iso_size | ".format(percent))
            else:
                print(result_output + '\033[91mFAIL\033[0m')
                return False

            del_iso()

        print(result_output + '\033[92mPASS\033[0m')
        log.info('pass')
        return True

    @staticmethod
    def fs_mark33_count(start=10, end=100, step=5, size=1024, mount_dir=STORAGE_MOUNT_DIR, scr_dir=SCRIPT_DIR):
        print("# TEST # <{}>:".format(Test.fs_mark33_count.__name__))

        # report_file = open('{}/{}'.format(REPORT_PATH, REPORT_FILENAME), 'w')
        # report_file.close()

        run_fs_mark = '{script_dir}/fs_mark-3.3/fs_mark -d {test_dir} -s {file_size} -n {file_count} -v'
        print('FSUse%        Count         Size    Files/sec     App Overhead        CREAT (Min/Avg/Max)        WRITE (Min/Avg/Max)        FSYNC (Min/Avg/Max)         SYNC (Min/Avg/Max)        CLOSE (Min/Avg/Max)       UNLINK (Min/Avg/Max)')
        for count in range(start, end, step):
            test = subprocess.run(run_fs_mark.format(script_dir=scr_dir,
                                                     test_dir=mount_dir,
                                                     file_size=str(size),
                                                     file_count=str(count)),
                                  shell=True,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
            out = linesep.join([s for s in test.stdout.decode("utf-8").splitlines() if s])
            err = linesep.join([s for s in test.stdout.decode("utf-8").splitlines() if s])

            if test.returncode == 0:
                with open('{}/{}'.format(REPORT_PATH, REPORT_FILENAME), 'a+') as report_file:
                    report_file.write(out.splitlines()[-1]+'\n')
                print("{} | \033[92mpass\033[0m".format(out.splitlines()[-1]))
                log.info("{} | ".format(out))
            else:
                print('{} | \033[91mfail\033[0m'.format(err))
                log.info("{} | ".format(err))
                return False

        log.info('pass')
        return True

    @staticmethod
    def fs_mark33_size(start=1024, end=10240, step=1024, count=1000, mount_dir=STORAGE_MOUNT_DIR, scr_dir=SCRIPT_DIR):
        print("# TEST # <{}>:".format(Test.fs_mark33_size.__name__))

        report_file = open('{}/{}'.format(REPORT_PATH, REPORT_FILENAME), 'w')
        report_file.close()

        run_fs_mark = '{script_dir}/fs_mark-3.3/fs_mark -d {test_dir} -s {file_size} -n {file_count} -v'
        print('FSUse%        Count         Size    Files/sec     App Overhead        CREAT (Min/Avg/Max)        WRITE (Min/Avg/Max)        FSYNC (Min/Avg/Max)         SYNC (Min/Avg/Max)        CLOSE (Min/Avg/Max)       UNLINK (Min/Avg/Max)')
        for size in range(start, end, step):
            test = subprocess.run(run_fs_mark.format(script_dir=scr_dir,
                                                     test_dir=mount_dir,
                                                     file_size=str(size),
                                                     file_count=str(count)),
                                  shell=True,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE)
            out = linesep.join([s for s in test.stdout.decode("utf-8").splitlines() if s])
            err = linesep.join([s for s in test.stdout.decode("utf-8").splitlines() if s])

            if test.returncode == 0:
                with open('{}/{}'.format(REPORT_PATH, REPORT_FILENAME), 'a+') as report_file:
                    report_file.write(out.splitlines()[-1]+'\n')
                print("{} | \033[92mpass\033[0m".format(out.splitlines()[-1]))
                log.info("{} | ".format(out))
            else:
                print('{} | \033[91mfail\033[0m'.format(err))
                log.info("{} | ".format(err))
                return False

        log.info('pass')
        return True


class TestSet(Test):
    def __init__(self,
                 file_count=5000, start_burder=40, end_burder=70, step=5, test_timeout=600,
                 th_file_count=5000, th_start_burder=15, th_end_burder=25, th_step=5, th_test_timeout=600):

        # параметры для однопоточных тестов
        self.fc = file_count
        self.sb = start_burder
        self.eb = end_burder
        self.s = step
        self.tt = test_timeout

        # параметры для многопоточных тестов
        self.th_fc = th_file_count
        self.th_sb = th_start_burder
        self.th_eb = th_end_burder
        self.th_s = th_step
        self.th_tt = th_test_timeout

    def th_test(self, function):
        function(count=self.th_fc, start=self.th_sb, end=self.th_eb, step=self.th_s)
    '''
        Базовый тест 1.
        Средняя загрузка разными файлами на r/w.
        1 поток
    '''
    def test_1_base_load(self):
        # print("### - TEST - ### <{}>:".format(TestSet.test_1.__name__), end=' ')
        log.info(TestSet.test_1_base_load.__name__)

        Test.file_filling(count=self.fc, start=self.sb, end=self.eb, step=self.s)
        Test.symlink_filling(count=self.fc, start=self.sb, end=self.eb, step=self.s)
        Test.hardlink_filling(count=self.fc, start=self.sb, end=self.eb, step=self.s)
        Test.arch_filling(count=self.fc, start=self.sb, end=self.eb, step=self.s)
        Test.iso_filling(count=self.fc, start=self.sb, end=self.eb, step=self.s)
    '''
        Базовый тест 2.
        Средняя загрузка разными файлами на r/w на протяжении времени.
        1 поток
    '''
    def test_2_timeout(self):
        # print("### - TEST - ### <{}>:".format(TestSet.test_2.__name__))
        log.info(TestSet.test_2_timeout.__name__)

        start_time = time()
        execute_time = 0
        while execute_time < self.tt:

            Test.file_filling(count=self.fc, start=self.sb, end=self.eb, step=self.s)
            Test.symlink_filling(count=self.fc, start=self.sb, end=self.eb, step=self.s)
            Test.hardlink_filling(count=self.fc, start=self.sb, end=self.eb, step=self.s)
            Test.arch_filling(count=self.fc, start=self.sb, end=self.eb, step=self.s)
            Test.iso_filling(count=self.fc, start=self.sb, end=self.eb, step=self.s)

            end_time = time()
            execute_time += end_time - start_time
    '''
        Базовый тест 3.
        Средняя загрузка разными файлами на r/w.
        3 потока
    '''
    def test_3_threads(self):
        # print("### - TEST - ### <{}>:".format(TestSet.test_3.__name__))
        log.info(TestSet.test_3_threads.__name__)

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            executor.map(self.th_test, [Test.iso_filling,
                                        Test.symlink_filling,
                                        Test.hardlink_filling])
    '''
        Базовый тест 4.
        Средняя загрузка разными файлами на r/w.
        4 потока
    '''
    def test_4_threads(self):
        # print("### - TEST - ### <{}>:".format(TestSet.test_4.__name__))
        log.info(TestSet.test_4_threads.__name__)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            executor.map(self.th_test, [Test.iso_filling,
                                        Test.symlink_filling,
                                        Test.hardlink_filling,
                                        Test.arch_filling])
    '''
        Базовый тест 5.
        Средняя загрузка разными файлами на r/w.
        5 потоков
    '''
    def test_5_threads(self):
        # print("### - TEST - ### <{}>:".format(TestSet.test_5.__name__))
        log.info(TestSet.test_5_threads.__name__)

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            executor.map(self.th_test, [Test.iso_filling,
                                        Test.symlink_filling,
                                        Test.hardlink_filling,
                                        Test.arch_filling,
                                        Test.iso_filling])
    '''
        Базовый тест 6.
        Большие файлы.
    '''
    def test_6_big_files(self):
        # print("### - TEST - ### <{}>:".format(TestSet.test_6.__name__))
        log.info(TestSet.test_6_big_files.__name__)

        Test.big_file_copying(start=self.sb, end=self.eb, step=self.s)
        Test.big_arch_copying(start=self.sb, end=self.eb, step=self.s)
        Test.big_iso_copying(start=self.sb, end=self.eb, step=self.s)

    '''
        Базовый тест 7.
        Бенчмарк FS_mark-3.3 
    '''
    def test_7_fs_mark33_count(self):
        # print("### - TEST - ### <{}>:".format(TestSet.test_7.__name__))
        log.info(TestSet.test_7_fs_mark33_count.__name__)

        Test.fs_mark33_count(start=self.sb, end=self.eb, step=self.s)

    def test_8_fs_mark33_size(self):
        # print("### - TEST - ### <{}>:".format(TestSet.test_7.__name__))
        log.info(TestSet.test_8_fs_mark33_size.__name__)

        Test.fs_mark33_size(start=self.sb, end=self.eb, step=self.s, count=self.fc)