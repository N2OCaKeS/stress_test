import os
import pwd
import pexpect
import pdb

from sys import exit
from pathlib import Path
from time import sleep, ctime, time
from multiprocessing import Process
from aub_conf import PSAUD_PROC_BODYS, USERAUD_PROC_BODYS, FILEAUD_PROC_BODYS, TEST_USER
from libs.libaub import Auditd, CheckAusearch, Prepare, User, UnixUser, cmd


class AuditdTest(Auditd, CheckAusearch):
    def __init__(self, procs, do_positive_test=True, do_negative_test=False):
        '''
        :param do_positive_test: Необходимость принудительной инициализации событий 'success=yes'
        :param do_negative_test: Необходимость принудительной инициализации событий 'success=no'
        :param procs: dict = {'event_flag': ('success_cmd', 'failure_cmd'),
        '''
        self.__pos = do_positive_test
        self.__neg = do_negative_test
        self.__procs = procs

        self.useraud_search_pattern = ''

    @staticmethod
    def _as_another_user(uid, gid=None):  # optional group
        def wrapper(func):
            def wrapped(*args, **kwargs):
                with UnixUser(uid, gid):
                    return func(*args, **kwargs)  # execute the function
            return wrapped
        return wrapper

    def _neg_files_prep(self, event_flag):
        pass

    def _neg_clean(self, event_flag):
        pass

    def _template_ps_psaud_timer(self,
                                 syscall,
                                 life_time,
                                 delay,
                                 number=None,
                                 user=None):
        '''
        :param syscall: наименование события audit
        :param life_time: время жизни процесса
        :param delay: периодичность генерации события audit
        :param number: количество процессов
        :return:
        '''
        if number is None:
            number = 0
        timer_file = '/tmp/timer' + str(number)
        target = '/tmp/file' + str(number)
        target_dir = '/tmp/dir' + str(number)

        while life_time > 0:
            sleep(delay)
            if self.__pos:
                with open(timer_file, 'w') as file:
                    file.write(ctime())

                try:
                    if syscall in ('open', 'delete', 'chmod', 'chown'):
                        file = open(target, 'w')
                        file.close()
                        os.chmod(target, 0o777)
                    if syscall in ('audit', 'acl'):
                        os.mkdir(target_dir, 0o777)
                        target = target_dir
                    if syscall == 'mount':
                        os.mkdir(target_dir, 0o777)
                        os.mkdir(target_dir + 'mount', 0o777)
                        target = '{dir} {dir}mount'.format(dir=target_dir)
                    if syscall in ('exec', 'module', 'cap', 'net', 'uid', 'gid'):
                        target = ''
                    if syscall == 'mac':
                        os.mkdir(target_dir, 0o777)
                        target = target_dir
                    if syscall == 'rename':
                        file = open(target, 'w')
                        file.close()
                        os.chmod(target, 0o777)
                        target = target+' '+target+str(number)
                except (FileNotFoundError, FileExistsError):
                    pass

                cmd(self.__procs[syscall][0] + ' ' + target)

                if syscall == 'mount':
                    cmd('umount {}mount &> /dev/null'.format(target_dir))
                if syscall == 'module':
                    cmd(PSAUD_PROC_BODYS[syscall][0] + ' -r')
                if syscall in ('open', 'chmod', 'chown', 'audit', 'acl', 'mac', 'mount', 'cap', 'rename'):
                    cmd('rm -rf /tmp/dir*')
                    cmd('rm -rf /tmp/file*')
                target = '/tmp/file' + str(number)

            if self.__neg:
                pass
            life_time -= delay

    def _template_ps_psaud_counter(self,
                                   syscall,
                                   life_time,
                                   delay,
                                   number=None,
                                   user=None):
        '''
        :param syscall: наименование события audit
        :param life_time: время жизни процесса
        :param delay: периодичность генерации события audit
        :param number: количество процессов
        :return:
        '''
        if number is None:
            number = 0
        counter_file = '/tmp/counter' + str(number)
        target = '/tmp/file' + str(number)
        target_dir = '/tmp/dir' + str(number)
        target_cmd = '/tmp/cmd' + str(number)

        counter = 0
        while life_time > 0:
            sleep(delay)
            if self.__pos:

                try:
                    if syscall in ('open', 'delete', 'chmod', 'chown'):
                        file = open(target, 'w')
                        file.close()
                        os.chmod(target, 0o777)
                    if syscall in ('audit', 'acl'):
                        os.mkdir(target_dir, 0o777)
                        target = target_dir
                    if syscall == 'mount':
                        os.mkdir(target_dir, 0o777)
                        os.mkdir(target_dir + 'mount', 0o777)
                        target = '{dir} {dir}mount'.format(dir=target_dir)
                    if syscall in ('exec', 'module', 'cap', 'net', 'uid', 'gid'):
                        target = ''
                    if syscall == 'mac':
                        os.mkdir(target_dir, 0o777)
                        target = target_dir
                    if syscall == 'rename':
                        file = open(target, 'w')
                        file.close()
                        os.chmod(target, 0o777)
                        target = target+' '+target+str(number)
                except (FileNotFoundError, FileExistsError):
                    pass

                completed_cmd = cmd(self.__procs[syscall][0] + ' ' + target)
                if completed_cmd.returncode != 2:  # обрабатываем результат
                    counter += 1
                    with open(counter_file, 'w') as file:
                        file.write(str(counter))

                if syscall == 'mount':
                    cmd('umount {}mount &> /dev/null'.format(target_dir))
                if syscall == 'module':
                    cmd(USERAUD_PROC_BODYS[syscall][0] + ' -r')
                if syscall in ('open', 'chmod', 'chown', 'audit', 'acl', 'mac', 'mount', 'cap', 'rename'):
                    cmd('rm -rf /tmp/dir*')
                    cmd('rm -rf /tmp/file*')
                target = '/tmp/file' + str(number)

            if self.__neg:
                pass
            life_time -= delay

    def _template_ps_useraud_timer(self,
                                   syscall,
                                   life_time,
                                   delay,
                                   number=None,
                                   user=None):
        if number is None:
            number = 0
        timer_file = '/tmp/timer' + str(number)
        target = '/tmp/file' + str(number)
        target_dir = '/tmp/dir' + str(number)
        target_cmd = '/tmp/cmd' + str(number)
        uid = pwd.getpwnam(user).pw_uid
        gid = pwd.getpwnam(user).pw_gid

        while life_time > 0:
            sleep(delay)
            if self.__pos:
                with open(timer_file, 'w') as file:
                    file.write(ctime())

                try:
                    if syscall in ('open', 'delete', 'chmod', 'chown'):
                        file = open(target, 'w')
                        file.close()
                        os.chmod(target, 0o777)
                    if syscall in ('audit', 'acl'):
                        os.mkdir(target_dir, 0o777)
                        target = target_dir
                    if syscall == 'mount':
                        os.mkdir(target_dir, 0o777)
                        os.mkdir(target_dir + 'mount', 0o777)
                        target = '{dir} {dir}mount'.format(dir=target_dir)
                    if syscall in ('exec', 'module', 'cap', 'net', 'uid', 'gid'):
                        target = ''
                    if syscall == 'mac':
                        target = '/file' + str(number)
                        file = open(target, 'w')
                        file.close()
                    if syscall == 'rename':
                        file = open(target, 'w')
                        file.close()
                        os.chmod(target, 0o777)
                        target = target+' '+target+str(number)
                except (FileNotFoundError, FileExistsError):
                    pass

                with open(target_cmd, 'w') as cmd_file:
                    cmd_file.write(self.__procs[syscall][0] + ' ' + target)

                cmd('su {} -c "{} {}"'.format(user, self.__procs[syscall][0], target))
                # completed_cmd = cmd('su {} -c "{} {}"'.format(user, self.__procs[syscall][0], target))
                # if completed_cmd.returncode != 0:
                #     print(cmd('ls /tmp/'))
                #     print(completed_cmd)
                #     exit(2)

                # self._pos_useraud_clean(syscall)
                if syscall in ('uid', 'gid'):
                    cmd('usermod -u {} -g {} {}'.format(uid, gid, user))
                if syscall == 'mount':
                    cmd('umount {}mount &> /dev/null'.format(target_dir))
                if syscall == 'module':
                    cmd(USERAUD_PROC_BODYS[syscall][0] + ' -r')
                if syscall in ('open', 'chmod', 'chown', 'audit', 'acl', 'mac', 'mount', 'cap', 'rename'):
                    cmd('rm -rf /tmp/dir*')
                    cmd('rm -rf /tmp/file*')
                target = '/tmp/file' + str(number)

            if self.__neg:
                pass
            life_time -= delay

    def _template_ps_useraud_counter(self,
                                     syscall,
                                     life_time,
                                     delay,
                                     number=None,
                                     user=None):
        '''
        :param syscall: наименование события audit
        :param life_time: время жизни процесса
        :param delay: периодичность генерации события audit
        :param number: количество процессов
        :return:
        '''
        if number is None:
            number = 0
        counter_file = '/tmp/counter' + str(number)
        target = '/tmp/file' + str(number)
        target_dir = '/tmp/dir' + str(number)
        target_cmd = '/tmp/cmd' + str(number)
        uid = pwd.getpwnam(user).pw_uid
        gid = pwd.getpwnam(user).pw_gid

        counter = 0
        while life_time > 0:
            sleep(delay)
            if self.__pos:

                try:
                    if syscall in ('open', 'delete', 'chmod', 'chown'):
                        file = open(target, 'w')
                        file.close()
                        os.chmod(target, 0o777)
                    if syscall in ('audit', 'acl'):
                        os.mkdir(target_dir, 0o777)
                        target = target_dir
                    if syscall == 'mount':
                        os.mkdir(target_dir, 0o777)
                        os.mkdir(target_dir + 'mount', 0o777)
                        target = '{dir} {dir}mount'.format(dir=target_dir)
                    if syscall in ('exec', 'module', 'cap', 'net', 'uid', 'gid'):
                        target = ''
                    if syscall == 'mac':
                        target = '/file' + str(number)
                        file = open(target, 'w')
                        file.close()
                    if syscall == 'rename':
                        file = open(target, 'w')
                        file.close()
                        os.chmod(target, 0o777)
                        target = target + ' ' + target + str(number)
                except (FileNotFoundError, FileExistsError):
                    pass

                completed_cmd = cmd('su {} -c "{} {}"'.format(user, self.__procs[syscall][0], target))
                if completed_cmd.returncode != 2:  # обрабатываем результат
                    counter += 1
                    with open(counter_file, 'w') as file:
                        file.write(str(counter))
                    with open(target_cmd, 'w') as cmd_file:
                        cmd_file.write(self.__procs[syscall][0] + ' ' + target)

                # self._pos_useraud_clean(syscall)
                if syscall in ('uid', 'gid'):
                    cmd('usermod -u {} -g {} {}'.format(uid, gid, user))
                if syscall == 'mount':
                    cmd('umount {}mount &> /dev/null'.format(target_dir))
                if syscall == 'module':
                    cmd(USERAUD_PROC_BODYS[syscall][0] + ' -r')
                if syscall in ('open', 'chmod', 'chown', 'audit', 'acl', 'mac', 'mount', 'cap', 'rename'):
                    cmd('rm -rf /tmp/dir*')
                    cmd('rm -rf /tmp/file*')
                target = '/tmp/file' + str(number)

            if self.__neg:
                pass
            life_time -= delay

    def _template_ps_fileaud_timer(self,
                                   syscall,
                                   life_time,
                                   delay,
                                   number=None,
                                   user=None):
        '''
        :param syscall: наименование события audit
        :param life_time: время жизни процесса
        :param delay: периодичность генерации события audit
        :param number: количество процессов
        :return:
        '''
        if number is None:
            timer_file = '/tmp/timer'
            target_file = '/tmp/file_' + syscall
        else:
            timer_file = '/tmp/timer' + str(number)
            target_file = '/tmp/file_' + syscall + str(number)

        file = open(target_file, 'w')
        file.close()
        if syscall == 'create':
            cmd('setfaud -m u:0:+{ef}:+{ef} /tmp/'.format(ef=syscall))
        if syscall == 'exec':
            with open(target_file, 'w') as file:
                file.writelines(['#!/bin/bash\n',
                                 'echo true &> /dev/null\n'])
            cmd('chmod +x '+ target_file)
        else:
            cmd('setfaud -m u:0:+{ef}:+{ef} {tf}'.format(ef=syscall, tf=target_file))

        while life_time > 0:
            sleep(delay)
            if self.__pos:
                with open(timer_file, 'w') as file:
                    file.write(ctime())
                cmd(self.__procs[syscall][0] + target_file)
            if self.__neg:
                pass
            life_time -= delay

        cmd('setfaud -X ' + target_file)

    def _template_ps_fileaud_counter(self,
                                     syscall,
                                     life_time,
                                     delay,
                                     number=None,
                                     user=None):
        '''
        :param syscall: наименование события audit
        :param life_time: время жизни процесса
        :param delay: периодичность генерации события audit
        :param number: количество процессов
        :return:
        '''

        if number is None:
            counter_file = '/tmp/counter'
            target_file = '/tmp/file_' + syscall
        else:
            counter_file = '/tmp/counter' + str(number)
            target_file = '/tmp/file_' + syscall + str(number)

        file = open(target_file, 'w')
        file.close()
        if syscall == 'create':
            cmd('setfaud -m u:0:+{ef}:+{ef} /tmp/'.format(ef=syscall))
        if syscall == 'exec':
            with open(target_file, 'w') as file:
                file.writelines(['#!/bin/bash\n',
                                 'echo true\n'])
            cmd('chmod +x '+ target_file)
            cmd('setfaud -m u:0:+{ef}:+{ef} {tf}'.format(ef=syscall, tf=target_file))
        else:
            cmd('setfaud -m u:0:+{ef}:+{ef} {tf}'.format(ef=syscall, tf=target_file))

        counter = 0
        while life_time > 0:
            sleep(delay)
            if self.__pos:
                if syscall == 'delete':  # delete приходится создавать здесь и каждый раз :(
                    file = open(target_file, 'w')
                    file.close()
                    cmd('setfaud -m u:0:+{ef}:+{ef} {tf}'.format(ef=syscall, tf=target_file))
                if syscall == 'acl':
                    cmd('setfacl -b '+ target_file)

                # генерируем событие
                completed_cmd = cmd(self.__procs[syscall][0] + target_file)
                if completed_cmd.returncode == 0: # обрабатываем результат
                    counter += 1
                    with open(counter_file, 'w') as file:
                        file.write(str(counter))
                else:
                    try:
                        print(completed_cmd.stderr.decode('utf-8'))
                    except AttributeError:
                        pass

            if self.__neg:
                pass
            life_time -= delay

    @staticmethod
    def _create_ps(syscall,
                   func,
                   proc_lifetime,
                   delay,
                   count=None,
                   user=None):
        '''
        :param syscall: наименование события audit
        :param func: шаблон тела процесса _template_ps*
        :param proc_lifetime: время жизни процесса
        :param delay: периодичность генерации события audit
        :param count: количество процессов
        :return:
        '''

        if count is None:
            process = Process(name='test_process_{}'.format(syscall),
                              target=func,
                              args=(syscall, proc_lifetime, delay, count, user))
            return process
        else:
            print('{} event / sec'.format(int(float(proc_lifetime) / float(delay) * int(count))))
            processes_lst = []
            for index in range(count):
                processes_lst.append(Process(name='test_process_{}_{}'.format(syscall, index),
                                             target=func,
                                             args=(syscall, proc_lifetime, delay, index, user)))
            return processes_lst

    def test_get_latency_psaud(self,
                               audit_flag,
                               ps_lifetime=3,
                               event_re_initialization_delay=0.001,
                               accuracy=3):
        '''
        :param audit_flag: наименование события audit
        :param ps_lifetime: время жизни процесса
        :param event_re_initialization_delay: периодичность генерации события audit
        :param accuracy: порядок округления результатов
        :return:
        '''
        Auditd.clean()
        Prepare.clean()

        test_ps = self._create_ps(syscall=audit_flag,
                                  func=self._template_ps_psaud_timer,
                                  proc_lifetime=ps_lifetime,
                                  delay=event_re_initialization_delay)

        # запускаем процесс генератор
        test_ps.start()

        # навешиваем аудит
        cmd('psaud {pid} +{flag}:-{flag}'.format(pid=test_ps.pid, flag=(audit_flag)))

        # отсчет времени
        start, end = time(), time()

        # ищем событие
        while CheckAusearch.psaud(audit_flag, test_ps.pid) is False:
            end = time()
            if not test_ps.is_alive():
                return -1

        test_ps.terminate()

        return round(end - start, accuracy)

    def test_get_latency_psaud_under_load(self,
                                          audit_flag,
                                          count,
                                          ps_lifetime=None,
                                          event_re_initialization_delay=None,
                                          accuracy=3):
        '''
        :param audit_flag: наименование события audit
        :param count: количество процессов
        :param ps_lifetime: время жизни процесса
        :param event_re_initialization_delay: периодичность генерации события audit
        :param accuracy: порядок округления результатов
        :return:
        '''

        Auditd.clean()
        Prepare.clean()

        if ps_lifetime is None:
            ps_lifetime = count
        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_psaud_timer,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count)
        for test_ps in test_ps_lst:
            test_ps.start()
            cmd('psaud {pid} +{flag}:-{flag}'.format(pid=str(test_ps.pid), flag=(audit_flag)))

        last_test_ps = test_ps_lst[-1]
        last_timefile = '/tmp/timer' + str(count - 1)
        start, end = time(), time()
        while CheckAusearch.psaud(audit_flag, last_test_ps.pid, last_timefile) is False:
            end = time()
            if not last_test_ps.is_alive():
                return -1

        for test_ps in test_ps_lst:
            test_ps.terminate()

        return round(end - start, accuracy)

    def test_losses_psaud_under_load(self,
                                     audit_flag,
                                     count,
                                     ps_lifetime=None,
                                     event_re_initialization_delay=None):
        '''
        :param audit_flag: наименование события audit
        :param count: количество процессов
        :param ps_lifetime: время жизни процесса
        :param event_re_initialization_delay: периодичность генерации события audit
        :return:
        '''
        Auditd.clean()
        Prepare.clean()

        if ps_lifetime is None:
            ps_lifetime = count

        # инициализируем процессы
        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_psaud_counter,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count)

        # точка остчета времени
        start = ctime()

        # навешиваем аудит
        for test_ps in test_ps_lst:
            test_ps.start()
            cmd('psaud {pid} +{flag}:-{flag}'.format(pid=str(test_ps.pid), flag=audit_flag))

        # ждем смерть последнего процесса
        last_test_ps = test_ps_lst[-1]
        while last_test_ps.is_alive():
            pass

        # контрольно убиваем процессы
        for test_ps in test_ps_lst:
            test_ps.terminate()

        # считаем предполагаемое количество событий
        expected_event_amount = 0
        for file in Path('/tmp').glob('counter*'):
            with open(file, 'r') as f:
                expected_event_amount += int(f.read())
        #         os.remove(file)
        # if expected_event_amount == 0:
        #     return [False, False, False, False]

        # считаем суммарное количество событий замеченных auditd
        auditd_events_amount = 0
        for test_ps in test_ps_lst:
            auditd_events_amount += CheckAusearch.psaud_event_count(audit_flag, test_ps.pid, start)

        try:
            if auditd_events_amount / expected_event_amount > 1:
                res = 100.0
            else:
                res = round(round(auditd_events_amount / expected_event_amount, 2) * 100, 2)
        except ZeroDivisionError:
            return (expected_event_amount <= auditd_events_amount,
                    0,
                    auditd_events_amount,
                    0.0)

        return (expected_event_amount <= auditd_events_amount,
                expected_event_amount,
                auditd_events_amount,
                res)

    def test_get_latency_useraud(self,
                                 audit_flag,
                                 ps_lifetime=10,
                                 event_re_initialization_delay=1,
                                 accuracy=3,
                                 user=TEST_USER):
        '''
        :param audit_flag: наименование события audit
        :param ps_lifetime: время жизни процесса
        :param event_re_initialization_delay: периодичность генерации события audit
        :param accuracy: порядок округления результатов
        :return:
        '''
        Auditd.clean()
        User.add(user)
        if audit_flag == 'chown':
            User.add_to_group(user, 'users')
        if audit_flag == 'module':
            User.add_priv(user, '+16')
        if audit_flag == 'uid':
            User.add_priv(user, '+7')
        if audit_flag == 'gid':
            User.add_priv(user, '+6')
        if audit_flag == 'audit':
            User.add_priv(user, '+1')
        if audit_flag == 'mac':
            User.add_priv(user, '+3')
        if audit_flag == 'cap':
            User.add_priv(user, '+10')
        if audit_flag == 'chroot':
            User.add_priv(user, '+18')

        cmd('useraud -m {u} +{flag}'.format(u=user, flag=(audit_flag)))

        test_ps = self._create_ps(syscall=audit_flag,
                                  func=self._template_ps_useraud_timer,
                                  proc_lifetime=ps_lifetime,
                                  delay=event_re_initialization_delay,
                                  user=user)
        test_ps.start()
        last_timefile = '/tmp/timer0'
        last_cmdfile = '/tmp/cmd0'

        # забираем эту команду
        with open(last_cmdfile, 'r') as file:
            last_cmd = file.read()

        start, end = time(), time()  # засекаем время
        while CheckAusearch.useraud(last_cmd, last_timefile) is False:
            end = time()
            if not test_ps.is_alive():
                return -1

        test_ps.terminate()
        User.rm(user)

        return round(end - start, accuracy)

    def test_get_latency_useraud_under_load(self,
                                            audit_flag,
                                            count,
                                            ps_lifetime=None,
                                            event_re_initialization_delay=None,
                                            user=TEST_USER,
                                            accuracy=3):
        '''
        :param audit_flag: наименование события audit
        :param count: количество процессов
        :param ps_lifetime: время жизни процесса
        :param event_re_initialization_delay: периодичность генерации события audit
        :param accuracy: порядок округления результатов
        :return:
        '''

        Auditd.clean()
        Prepare.clean()

        User.add(user)
        if audit_flag == 'chown':
            User.add_to_group(user, 'users')
        if audit_flag == 'module':
            User.add_priv(user, '+16')
        if audit_flag == 'uid':
            User.add_priv(user, '+7')
        if audit_flag == 'gid':
            User.add_priv(user, '+6')
        if audit_flag == 'audit':
            User.add_priv(user, '+1')
        if audit_flag == 'mac':
            User.add_priv(user, '+3')
        if audit_flag == 'cap':
            User.add_priv(user, '+10')
        if audit_flag == 'chroot':
            User.add_priv(user, '+18')

        cmd('useraud -m {u} +{flag}'.format(u=user, flag=(audit_flag)))

        if ps_lifetime is None:
            ps_lifetime = count
        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_useraud_timer,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count,
                                      user=user)

        last_test_ps = test_ps_lst[-1]
        last_timefile = '/tmp/timer' + str(count - 1)
        last_cmdfile = '/tmp/cmd' + str(count - 1)

        # запускаем процессы-генераторы
        for test_ps in test_ps_lst:
            test_ps.start()

        # ждем появления команды от последнего процесса
        while os.path.exists(last_cmdfile) is False:
            sleep(event_re_initialization_delay)
        # забираем эту команду
        with open('/tmp/cmd' + str(count - 1), 'r') as file:
            last_cmd = file.read()

        start, end = time(), time() #  засекаем время
        while CheckAusearch.useraud(last_cmd, last_timefile) is False:
            end = time()
            if not last_test_ps.is_alive():
                return -1

        # убить все
        for test_ps in test_ps_lst:
            test_ps.terminate()

        User.rm_priv(user)
        User.rm(user)
        return round(end - start, accuracy)

    def test_losses_useraud_under_load(self,
                                       audit_flag,
                                       count,
                                       ps_lifetime=None,
                                       event_re_initialization_delay=0.001,
                                       user=TEST_USER):
        '''
        :param audit_flag: наименование события audit
        :param count: количество процессов
        :param ps_lifetime: время жизни процесса
        :param event_re_initialization_delay: периодичность генерации события audit
        :return:
        '''
        Auditd.clean()
        Prepare.clean()
        User.add(user)
        if audit_flag == 'chown':
            User.add_to_group(user, 'users')
        if audit_flag == 'module':
            User.add_priv(user, '+16')
        if audit_flag == 'uid':
            User.add_priv(user, '+7')
        if audit_flag == 'gid':
            User.add_priv(user, '+6')
        if audit_flag == 'audit':
            User.add_priv(user, '+1')
        if audit_flag == 'mac':
            User.add_priv(user, '+3')
        if audit_flag == 'cap':
            User.add_priv(user, '+10')
        if audit_flag == 'chroot':
            User.add_priv(user, '+18')

        # навешиваем аудит
        cmd('useraud -m {u} +{flag}'.format(u=user, flag=(audit_flag)))

        if ps_lifetime is None:
            ps_lifetime = count

        # инициализируем процессы
        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_useraud_counter,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count,
                                      user=user)

        # точка остчета времени
        start = ctime()

        # запускаем процессы-генераторы
        for test_ps in test_ps_lst:
            test_ps.start()

        # ждем смерть последнего процесса
        last_test_ps = test_ps_lst[-1]
        while last_test_ps.is_alive():
            pass

        # контрольно убиваем процессы
        for test_ps in test_ps_lst:
            test_ps.terminate()

        # считаем предполагаемое количество событий
        expected_event_amount = 0
        for file in Path('/tmp').glob('counter*'):
            with open(file, 'r') as f:
                expected_event_amount += int(f.read())
                # os.remove(file)
        # if expected_event_amount == 0:
        #     return [False, False, False, False]

        # забираем команду
        with open('/tmp/cmd' + str(count - 1), 'r') as file:
            last_cmd = file.read()

        # считаем суммарное количество событий замеченных auditd
        auditd_events_amount = 0
        for index in range(count):
            auditd_events_amount += CheckAusearch.useraud_event_count(last_cmd, start)

        try:
            if auditd_events_amount / expected_event_amount > 1:
                res = 100.0
            else:
                res = round(round(auditd_events_amount / expected_event_amount, 2) * 100, 2)
        except ZeroDivisionError:
            return (expected_event_amount <= auditd_events_amount,
                    0,
                    auditd_events_amount,
                    0.0)

        User.rm_priv(user)
        User.rm(user)

        return (expected_event_amount <= auditd_events_amount,
                expected_event_amount,
                auditd_events_amount,
                res)

    def test_get_latency_fileaud(self,
                                 audit_flag,
                                 ps_lifetime=3,
                                 event_re_initialization_delay=0.001,
                                 accuracy=3):

        Auditd.clean()

        test_ps = self._create_ps(syscall=audit_flag,
                                  func=self._template_ps_fileaud_timer,
                                  proc_lifetime=ps_lifetime,
                                  delay=event_re_initialization_delay)
        test_ps.start()

        target_file = 'file_'+audit_flag
        start, end = time(), time()
        while CheckAusearch.fileaud(target_file) is False:
            end = time()
            if not test_ps.is_alive():
                return -1

        test_ps.terminate()
        Prepare.clean()

        return round(end - start, accuracy)

    def test_get_latency_fileaud_under_load(self,
                                            audit_flag,
                                            count,
                                            ps_lifetime=None,
                                            event_re_initialization_delay=None,
                                            accuracy=3):

        Auditd.clean()

        if ps_lifetime is None:
            ps_lifetime = count
        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_fileaud_timer,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count)
        for test_ps in test_ps_lst:
            test_ps.start()

        last_test_ps = test_ps_lst[-1]
        last_timefile = '/tmp/timer' + str(count - 1)
        target_file = 'file_' + audit_flag + str(count - 1)

        start, end = time(), time()
        while CheckAusearch.fileaud(target_file, last_timefile) is False:
            end = time()
            if not last_test_ps.is_alive():
                return -1

        for test_ps in test_ps_lst:
            test_ps.terminate()

        Prepare.clean()
        return round(end - start, accuracy)

    def test_get_losses_fileaud_under_load(self,
                                           audit_flag,
                                           count,
                                           ps_lifetime=None,
                                           event_re_initialization_delay=None,
                                           accuracy=3):
        Auditd.clean()
        Prepare.clean()

        if ps_lifetime is None:
            ps_lifetime = count

        # инициализируем процессы
        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_fileaud_counter,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count)

        # точка остчета времени
        start = ctime()

        # запускаем процессы
        for test_ps in test_ps_lst:
            test_ps.start()

        # ждем смерть последнего процесса
        last_test_ps = test_ps_lst[-1]
        while last_test_ps.is_alive():
            pass

        # контрольно убиваем процессы
        for test_ps in test_ps_lst:
            test_ps.terminate()

        # считаем предполагаемое количество событий
        expected_event_amount = 0
        for file in Path('/tmp').glob('counter*'):
            with open(file, 'r') as f:
                expected_event_amount += int(f.read())
        # if expected_event_amount == 0:
        #     return [False, False, False, False]

        # считаем суммарное количество событий замеченных auditd
        auditd_events_amount = 0
        for index in range(count):
            auditd_events_amount += CheckAusearch.fileaud_event_count('file_' + audit_flag + str(index), start)

        try:
            if auditd_events_amount / expected_event_amount > 1:
                res = 100.0
            else:
                res = round(round(auditd_events_amount / expected_event_amount, 2) * 100, 2)
        except ZeroDivisionError:
            return (expected_event_amount <= auditd_events_amount,
                    0,
                    auditd_events_amount,
                    0.0)

        Prepare.clean()
        return (expected_event_amount <= auditd_events_amount,
                expected_event_amount,
                auditd_events_amount,
                res)


class AuditdTestSet():

########################################################################################################################

    @staticmethod # ОТЛАЖЕНО
    def get_latency_psaud_single(event_flag, report_file):
        __audit_test = AuditdTest(PSAUD_PROC_BODYS)
        result = __audit_test.test_get_latency_psaud(event_flag)
        with open(report_file, 'w') as file:
            file.write('{} - {} sec\n'.format(event_flag, result))
        print('{} - {} sec'.format(event_flag, result))


    @staticmethod # ОТЛАЖЕНО
    def get_latency_psaud_total(event_flag_lst, report_file):
        __audit_test = AuditdTest(PSAUD_PROC_BODYS)
        for event_flag in event_flag_lst:

            result = __audit_test.test_get_latency_psaud(event_flag)

            with open(report_file, 'a+') as file:
                file.write('{} - {} sec\n'.format(event_flag, result))
            print('{} - {} sec'.format(event_flag, result))

    @staticmethod # ОТЛАЖЕНО
    def get_latency_stat_psaud(event_flag,
                               ps_count,
                               ps_lifetime,
                               ps_event_re_initialization_delay,
                               report_file):

        __audit_test = AuditdTest(PSAUD_PROC_BODYS)
        result = __audit_test.test_get_latency_psaud_under_load(event_flag,
                                                                ps_count,
                                                                ps_lifetime,
                                                                ps_event_re_initialization_delay)
        with open(report_file, 'a+') as file:
            file.write('{} {}\n'.format(event_flag, result))
        print('{} - {} sec'.format(event_flag, result))

    @staticmethod # ОТЛАЖЕНО
    def get_losses_stat_psaud(event_flag,
                              ps_count,
                              ps_lifetime,
                              ps_event_re_initialization_delay,
                              report_file):

        __audit_test = AuditdTest(PSAUD_PROC_BODYS)
        result = __audit_test.test_losses_psaud_under_load(event_flag,
                                                           ps_count,
                                                           ps_lifetime,
                                                           ps_event_re_initialization_delay)
        with open(report_file, 'a+') as file:
            file.write('{f} {eps} {exp_ev_am} {aud_ev_am} {prct_res} {bool_res}\n'.format(f=event_flag,
                                                                                          eps=int(float(ps_lifetime) / float(ps_event_re_initialization_delay) * int(ps_count)),
                                                                                          exp_ev_am=result[1],
                                                                                          aud_ev_am=result[2],
                                                                                          prct_res=result[3],
                                                                                          bool_res=result[0]))
        print('{f} {eps} {exp_ev_am} {aud_ev_am} {prct_res} {bool_res}\n'.format(f=event_flag,
                                                                                 eps=int(float(ps_lifetime) / float(ps_event_re_initialization_delay) * int(ps_count)),
                                                                                 exp_ev_am=result[1],
                                                                                 aud_ev_am=result[2],
                                                                                 prct_res=result[3],
                                                                                 bool_res=result[0]))

########################################################################################################################
    @staticmethod
    def get_latency_useraud_single(event_flag, report_file):
        __audit_test = AuditdTest(USERAUD_PROC_BODYS)

        result = __audit_test.test_get_latency_useraud(audit_flag=event_flag,
                                                       user=TEST_USER)
        with open(report_file, 'w') as file:
            file.write('{} - {} sec\n'.format(event_flag, result))
        print('{} - {} sec'.format(event_flag, result))

    @staticmethod
    def get_latency_useraud_total(event_flag_lst, report_file):
        __audit_test = AuditdTest(USERAUD_PROC_BODYS)
        for event_flag in event_flag_lst:

            result = __audit_test.test_get_latency_useraud(audit_flag=event_flag,
                                                           user=TEST_USER)
            with open(report_file, 'a+') as file:
                file.write('{} - {} sec\n'.format(event_flag, result))
            print('{} - {} sec'.format(event_flag, result))

    @staticmethod
    def get_latency_stat_useraud(event_flag,
                                 ps_count,
                                 ps_lifetime,
                                 ps_event_re_initialization_delay,
                                 report_file,
                                 user):

        __audit_test = AuditdTest(USERAUD_PROC_BODYS)
        result = __audit_test.test_get_latency_useraud_under_load(event_flag,
                                                                  ps_count,
                                                                  ps_lifetime,
                                                                  ps_event_re_initialization_delay,
                                                                  user)
        with open(report_file, 'a+') as file:
            file.write('{} {}\n'.format(event_flag, result))
        print('{} - {} sec'.format(event_flag, result))

    @staticmethod
    def get_losses_stat_useraud(event_flag,
                                ps_count,
                                ps_lifetime,
                                ps_event_re_initialization_delay,
                                report_file,
                                user):

        __audit_test = AuditdTest(USERAUD_PROC_BODYS)
        result = __audit_test.test_losses_useraud_under_load(event_flag,
                                                             ps_count,
                                                             ps_lifetime,
                                                             ps_event_re_initialization_delay,
                                                             user)
        with open(report_file, 'a+') as file:
            file.write('{f} {eps} {exp_ev_am} {aud_ev_am} {prct_res} {bool_res}\n'.format(f=event_flag,
                                                                                          eps=int(float(ps_lifetime) / float(ps_event_re_initialization_delay) * int(ps_count)),
                                                                                          exp_ev_am=result[1],
                                                                                          aud_ev_am=result[2],
                                                                                          prct_res=result[3],
                                                                                          bool_res=result[0]))
        print('{f} {eps} {exp_ev_am} {aud_ev_am} {prct_res} {bool_res}\n'.format(f=event_flag,
                                                                                 eps=int(float(ps_lifetime) / float(ps_event_re_initialization_delay) * int(ps_count)),
                                                                                 exp_ev_am=result[1],
                                                                                 aud_ev_am=result[2],
                                                                                 prct_res=result[3],
                                                                                 bool_res=result[0]))

########################################################################################################################
    @staticmethod # ОТЛАЖЕНО
    def get_latency_fileaud_single(event_flag, report_file):
        __audit_test = AuditdTest(FILEAUD_PROC_BODYS)
        result = __audit_test.test_get_latency_fileaud(event_flag)
        with open(report_file, 'w') as file:
            file.write('{} - {} sec\n'.format(event_flag, result))
        print('{} - {} sec'.format(event_flag, result))

    @staticmethod  # ОТЛАЖЕНО
    def get_latency_fileaud_total(event_flag_lst, report_file):
        __audit_test = AuditdTest(FILEAUD_PROC_BODYS)
        for event_flag in event_flag_lst:
            result = __audit_test.test_get_latency_fileaud(event_flag)
            with open(report_file, 'a+') as file:
                file.write('{} - {} sec\n'.format(event_flag, result))
            print('{} - {} sec'.format(event_flag, result))

    @staticmethod  # ОТЛАЖЕНО
    def get_latency_stat_fileaud(event_flag,
                                 ps_count,
                                 ps_lifetime,
                                 ps_event_re_initialization_delay,
                                 report_file,):

        __audit_test = AuditdTest(FILEAUD_PROC_BODYS)
        result = __audit_test.test_get_latency_fileaud_under_load(event_flag,
                                                                  ps_count,
                                                                  ps_lifetime,
                                                                  ps_event_re_initialization_delay)
        with open(report_file, 'a+') as file:
            file.write('{} {}\n'.format(event_flag, result))
        print('{} - {} sec'.format(event_flag, result))

    @staticmethod  # ОТЛАЖЕНО
    def get_losses_stat_fileaud(event_flag,
                                ps_count,
                                ps_lifetime,
                                ps_event_re_initialization_delay,
                                report_file,):
        __audit_test = AuditdTest(FILEAUD_PROC_BODYS)
        result = __audit_test.test_get_losses_fileaud_under_load(event_flag,
                                                                 ps_count,
                                                                 ps_lifetime,
                                                                 ps_event_re_initialization_delay)
        with open(report_file, 'a+') as file:
            file.write('{f} {eps} {exp_ev_am} {aud_ev_am} {prct_res} {bool_res}\n'.format(f=event_flag,
                                                                                          eps=int(float(
                                                                                              ps_lifetime) / float(
                                                                                              ps_event_re_initialization_delay) * int(
                                                                                              ps_count)),
                                                                                          exp_ev_am=result[1],
                                                                                          aud_ev_am=result[2],
                                                                                          prct_res=result[3],
                                                                                          bool_res=result[0]))
        print('{f} {eps} {exp_ev_am} {aud_ev_am} {prct_res} {bool_res}\n'.format(f=event_flag,
                                                                                 eps=int(float(ps_lifetime) / float(
                                                                                     ps_event_re_initialization_delay) * int(
                                                                                     ps_count)),
                                                                                 exp_ev_am=result[1],
                                                                                 aud_ev_am=result[2],
                                                                                 prct_res=result[3],
                                                                                 bool_res=result[0]))