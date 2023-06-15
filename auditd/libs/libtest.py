# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

from pwd import getpwnam
from os import mkdir, chmod, linesep
import subprocess
from time import sleep, ctime, time
from multiprocessing import Process, Manager
from aub_conf import PSAUD_PROC_BODYS, USERAUD_PROC_BODYS, FILEAUD_PROC_BODYS, TEST_USER
from libs.libaub import Auditd, CheckAusearch, Prepare, User, UnixUser, cmd
from libs.libaub import killer_ps

with open('git/stress_test/auditd/libs/stand_number.txt', 'r') as r:
    stand_number = r.read()


class AuditdTest(Auditd, CheckAusearch):

    def __init__(self,
                 procs,
                 do_positive_test=True,
                 do_negative_test=False):
        '''
        :param do_positive_test: Необходимость принудительной инициализации событий 'success=yes'
        :param do_negative_test: Необходимость принудительной инициализации событий 'success=no'
        :param procs: dict = {'event_flag': ('success_cmd', 'failure_cmd'),
        '''
        self.__pos = do_positive_test
        self.__neg = do_negative_test
        self.__procs = procs

    @staticmethod
    def _as_another_user(uid, gid=None):  # optional group
        def wrapper(func):
            def wrapped(*args, **kwargs):
                with UnixUser(uid, gid):
                    return func(*args, **kwargs)  # execute the function
            return wrapped
        return wrapper

    @staticmethod
    def _add_priv_prep(audit_flag, user):
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

    def _template_ps_psaud_timer(self,
                                 syscall,
                                 life_time,
                                 delay,
                                 number=None,
                                 user=None,
                                 timers=None,
                                 cmds=None,
                                 counters=None):
        '''
        :param syscall: наименование события audit
        :param life_time: время жизни процесса
        :param delay: периодичность генерации события audit
        :param number: количество процессов
        :return:
        '''
        if number is None:
            number = 0
        target = '/tmp/file' + str(number)
        target_dir = '/tmp/dir' + str(number)

        while life_time > 0:
            sleep(delay)
            if self.__pos:
                try:
                    timers[number] = ctime()
                except BrokenPipeError:
                    pass

                try:
                    if syscall in ('open', 'delete', 'chmod', 'chown'):
                        file = open(target, 'w')
                        file.close()
                        chmod(target, 0o777)
                    if syscall in ('audit', 'acl'):
                        mkdir(target_dir, 0o777)
                        target = target_dir
                    if syscall == 'mount':
                        mkdir(target_dir, 0o777)
                        mkdir(target_dir + 'mount', 0o777)
                        target = '{dir} {dir}mount'.format(dir=target_dir)
                    if syscall in ('exec', 'module', 'cap', 'net', 'uid', 'gid'):
                        target = ''
                    if syscall == 'mac':
                        mkdir(target_dir, 0o777)
                        target = target_dir
                    if syscall == 'rename':
                        file = open(target, 'w')
                        file.close()
                        chmod(target, 0o777)
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
                                   user=None,
                                   timers=None,
                                   cmds=None,
                                   counters=None):
        '''
        :param syscall: наименование события audit
        :param life_time: время жизни процесса
        :param delay: периодичность генерации события audit
        :param number: количество процессов
        :return:
        '''
        if number is None:
            number = 0
        target = '/tmp/file' + str(number)
        target_dir = '/tmp/dir' + str(number)

        counter = 0
        while life_time > 0:
            sleep(delay)
            if self.__pos:

                try:
                    if syscall in ('open', 'delete', 'chmod', 'chown'):
                        file = open(target, 'w')
                        file.close()
                        chmod(target, 0o777)
                    if syscall in ('audit', 'acl'):
                        mkdir(target_dir, 0o777)
                        target = target_dir
                    if syscall == 'mount':
                        mkdir(target_dir, 0o777)
                        mkdir(target_dir + 'mount', 0o777)
                        target = '{dir} {dir}mount'.format(dir=target_dir)
                    if syscall in ('exec', 'module', 'cap', 'net', 'uid', 'gid'):
                        target = ''
                    if syscall == 'mac':
                        mkdir(target_dir, 0o777)
                        target = target_dir
                    if syscall == 'rename':
                        file = open(target, 'w')
                        file.close()
                        chmod(target, 0o777)
                        target = target+' '+target+str(number)
                except (FileNotFoundError, FileExistsError):
                    pass

                completed_cmd = cmd(self.__procs[syscall][0] + ' ' + target)
                if completed_cmd.returncode != 2:  # обрабатываем результат
                    counter += 1
                    try:
                        counters[number] = counter
                    except BrokenPipeError:
                        pass

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
                                   user=None,
                                   timers=None,
                                   cmds=None,
                                   counters=None):

        if number is None:
            number = 0
        else:
            user = user + str(number)
        target = '/tmp/file' + str(number)
        target_dir = '/tmp/dir' + str(number)
        uid = getpwnam(user).pw_uid
        gid = getpwnam(user).pw_gid

        while life_time > 0:
            sleep(delay)
            if self.__pos:
                try:
                    timers[number] = ctime()
                except BrokenPipeError:
                    pass

                try:
                    if syscall in ('open', 'delete', 'chmod', 'chown'):
                        file = open(target, 'w')
                        file.close()
                        chmod(target, 0o777)
                    if syscall in ('audit', 'acl'):
                        mkdir(target_dir, 0o777)
                        target = target_dir
                    if syscall == 'mount':
                        mkdir(target_dir, 0o777)
                        mkdir(target_dir + 'mount', 0o777)
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
                        chmod(target, 0o777)
                        target = target+' '+target+str(number)
                except (FileNotFoundError, FileExistsError):
                    pass

                try:
                    cmds[number] = self.__procs[syscall][0] + ' ' + target
                except BrokenPipeError:
                    pass

                cmd('su {} -c "{} {}"'.format(user, self.__procs[syscall][0], target))

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
                                     user=None,
                                     timers=None,
                                     cmds=None,
                                     counters=None):
        '''
        :param syscall: наименование события audit
        :param life_time: время жизни процесса
        :param delay: периодичность генерации события audit
        :param number: количество процессов
        :return:
        '''
        if number is None:
            number = 0
        user = user + str(number)
        target = '/tmp/file' + str(number)
        target_dir = '/tmp/dir' + str(number)
        uid = getpwnam(user).pw_uid
        gid = getpwnam(user).pw_gid

        counter = 0
        while life_time > 0:
            sleep(delay)
            if self.__pos:

                try:
                    if syscall in ('open', 'delete', 'chmod', 'chown'):
                        file = open(target, 'w')
                        file.close()
                        chmod(target, 0o777)
                    if syscall in ('audit', 'acl'):
                        mkdir(target_dir, 0o777)
                        target = target_dir
                    if syscall == 'mount':
                        mkdir(target_dir, 0o777)
                        mkdir(target_dir + 'mount', 0o777)
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
                        chmod(target, 0o777)
                        target = target + ' ' + target + str(number)
                except (FileNotFoundError, FileExistsError):
                    pass

                completed_cmd = cmd('su {} -c "{} {}"'.format(user, self.__procs[syscall][0], target))
                if completed_cmd.returncode != 2:  # обрабатываем результат
                    counter += 1
                    try:
                        counters[number] = counter
                        cmds[number] = self.__procs[syscall][0] + ' ' + target
                    except BrokenPipeError:
                        pass

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
                                   user=None,
                                   timers=None,
                                   cmds=None,
                                   counters=None):
        '''
        :param syscall: наименование события audit
        :param life_time: время жизни процесса
        :param delay: периодичность генерации события audit
        :param number: количество процессов
        :return:
        '''
        if number is None:
            number = 0
            target_file = '/tmp/file_' + syscall + str(number)
        else:
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
        if syscall == 'mac':
            target_file = '/file_' + syscall + str(number)
            file = open(target_file, 'w')
            file.close()
        else:
            cmd('setfaud -m u:0:+{ef}:+{ef} {tf}'.format(ef=syscall, tf=target_file))

        while life_time > 0:
            sleep(delay)
            if self.__pos:
                if syscall == 'delete':  # delete приходится создавать здесь и каждый раз :(
                    file = open(target_file, 'w')
                    file.close()
                    cmd('setfaud -m u:0:+{ef}:+{ef} {tf}'.format(ef=syscall, tf=target_file))
                if syscall == 'acl':
                    cmd('setfacl -b ' + target_file)
                if syscall == 'mac':
                    cmd('/usr/sbin/pdpl-file 0:0:0 ' + target_file)

                try:
                    timers[number] = ctime()
                except BrokenPipeError:
                    pass
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
                                     user=None,
                                     timers=None,
                                     cmds=None,
                                     counters=None):
        '''
        :param syscall: наименование события audit
        :param life_time: время жизни процесса
        :param delay: периодичность генерации события audit
        :param number: количество процессов
        :return:
        '''

        if number is None:
            number = 0
            target_file = '/tmp/file_' + syscall + str(number)
        else:
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
        if syscall == 'mac':
            target_file = '/file_' + syscall + str(number)
            file = open(target_file, 'w')
            file.close()
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
                    cmd('setfacl -b ' + target_file)
                if syscall == 'mac':
                    cmd('/usr/sbin/pdpl-file 0:0:0 ' + target_file)

                # генерируем событие
                completed_cmd = cmd(self.__procs[syscall][0] + target_file)
                if completed_cmd.returncode != 2: # обрабатываем результат
                    counter += 1
                    try:
                        counters[number] = counter
                    except BrokenPipeError:
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
                   user=None,
                   timer_lst=None,
                   cmd_lst=None,
                   counters_lst=None):
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
                              args=(syscall, proc_lifetime, delay, count, user, timer_lst, cmd_lst, counters_lst))
            return process
        else:
            print('{} event / sec'.format(int(count) / float(delay)))
            processes_lst = []
            for index in range(count):
                processes_lst.append(Process(name='test_process_{}_{}'.format(syscall, index),
                                             target=func,
                                             args=(syscall, proc_lifetime, delay, index, user, timer_lst, cmd_lst, counters_lst)))
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

        manager = Manager()
        timers = manager.list([None])

        test_ps = self._create_ps(syscall=audit_flag,
                                  func=self._template_ps_psaud_timer,
                                  proc_lifetime=ps_lifetime,
                                  delay=event_re_initialization_delay,
                                  timer_lst=timers)

        # запускаем процесс генератор
        test_ps.start()

        # навешиваем аудит
        cmd('psaud {pid} +{flag}:-{flag}'.format(pid=test_ps.pid, flag=(audit_flag)))

        # отсчет времени
        start, end = time(), time()

        while timers[0] is None:
            pass

        # ищем событие по pid
        while CheckAusearch.psaud(audit_flag, test_ps.pid, timers[0]) is False:
            end = time()
            if not test_ps.is_alive():
                return 0

        test_ps.terminate()
        latency = end - start
        if latency < event_re_initialization_delay:
            latency = 0.001
        return round(latency, accuracy)

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

        manager = Manager()
        timers = manager.list([None]*count)

        killer_ps(stand_number)

        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_psaud_timer,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count,
                                      timer_lst=timers)
        print(test_ps_lst)
        for test_ps in test_ps_lst:
            test_ps.start()
            cmd('psaud {pid} +{flag}:-{flag}'.format(pid=str(test_ps.pid), flag=(audit_flag)))

        last_num = count - 1
        last_test_ps = test_ps_lst[-1]
        start, end = time(), time()  # засекаем время

        while True:
            try:
                if timers[last_num]:
                    break
            except Exception:
                pass
        while CheckAusearch.psaud(audit_flag, last_test_ps.pid, timers[last_num]) is False:
            end = time()
            if not last_test_ps.is_alive():
                return 0

        for test_ps in test_ps_lst:
            test_ps.terminate()

        latency = end - start
        if latency < event_re_initialization_delay:
            latency = 0.001
        return round(latency, accuracy)

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

        manager = Manager()
        counters = manager.list([0]*count)

        # инициализируем процессы
        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_psaud_counter,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count,
                                      counters_lst=counters)

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
        expected_event_amount = sum(counters)

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

        # создаем пользователя, навешиваем привилегии
        user = user+'0'
        Auditd.clean()
        User.add(user)
        self._add_priv_prep(audit_flag, user)
        cmd('useraud -m {u} +{flag}'.format(u=user, flag=(audit_flag)))

        # обЪявляем списки: со счетчиками, с командами
        # для каждого процесса n:
        #   timers[n] - его таймер
        #   cmds[n] - команда, которую он выполняет
        manager = Manager()
        timers = manager.list([None])
        cmds = manager.list([None])

        # инициализируем процесс
        test_ps = self._create_ps(syscall=audit_flag,
                                  func=self._template_ps_useraud_timer,
                                  proc_lifetime=ps_lifetime,
                                  delay=event_re_initialization_delay,
                                  user=user,
                                  timer_lst=timers,
                                  cmd_lst=cmds)
        test_ps.start()

        start, end = time(), time()  # засекаем время
        while timers[0] is None and cmds[0] is None:
            pass
        while CheckAusearch.useraud(cmds[0], timers[0]) is False:
            end = time()
            if not test_ps.is_alive():
                return 0

        test_ps.terminate()

        # удаляем пользователя
        User.rm_priv(user)
        User.rm(user)

        # возвращаем результат
        latency = end - start
        if latency < event_re_initialization_delay:
            latency = 0.001
        return round(latency, accuracy)

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

        # создаем пользователей, навешиваем привилегии
        for index in range(count):
            name = user + str(index)
            User.add(name)
            self._add_priv_prep(audit_flag, user)
            cmd('useraud -m {u} +{flag}'.format(u=name, flag=(audit_flag)))

        if ps_lifetime is None:
            ps_lifetime = count

        # обЪявляем списки: со счетчиками, с командами
        # для каждого процесса n:
        #   timers[n] - его таймер
        #   cmds[n] - команда, которую он выполняет
        manager = Manager()
        timers = manager.list([None]*count)
        cmds = manager.list([None]*count)

        killer_ps(stand_number)

        # инициализируем процессы
        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_useraud_timer,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count,
                                      user=user,
                                      timer_lst=timers,
                                      cmd_lst=cmds)

        last_test_ps = test_ps_lst[-1]
        last_num = count - 1

        # запускаем процессы-генераторы
        for test_ps in test_ps_lst:
            test_ps.start()

        start, end = time(), time()  # засекаем время
        while True:
            try:
                if timers[last_num] and cmds[last_num]:
                    break
            except Exception:
                pass

        # ждем появления команды от последнего процесса
        while CheckAusearch.useraud(cmds[last_num], timers[last_num]) is False:
            end = time() # пока команда, не появилась копим таймер

            # если последний запущенный процесс умер, а команда так и не появилась возвращаем 0
            if not last_test_ps.is_alive():
                #print('error')
                return 0

        # убить все
        for test_ps in test_ps_lst:
            test_ps.terminate()

        # удаляем пользователя
        for index in range(count):
            name = user + str(index)
            User.rm_priv(name)
            User.rm(name)

        # возвращаем результат
        latency = end - start
        if latency < event_re_initialization_delay:
            latency = 0.001
        return round(latency, accuracy)

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

        # создаем пользователей, навешиваем привилегии
        for index in range(count):
            name = user + str(index)
            User.add(name)
            self._add_priv_prep(audit_flag, user)
            cmd('useraud -m {u} +{flag}'.format(u=name, flag=(audit_flag)))

        if ps_lifetime is None:
            ps_lifetime = count

        # обЪявляем списки: со счетчиками, с командами
        # для каждого процесса n:
        #   counter[n] - его счетчик команд
        #   cmds[n] - команда, которую он выполняет
        manager = Manager()
        counters = manager.list([0]*count)
        cmds = manager.list([None]*count)

        # инициализируем процессы
        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_useraud_counter,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count,
                                      user=user,
                                      cmd_lst=cmds,
                                      counters_lst=counters)

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
        expected_event_amount = sum(counters)

        # считаем суммарное количество событий замеченных auditd
        auditd_events_amount = 0
        for index in range(count):
            # ищем команды, запущенные каждым процесса в логах audit
            auditd_events_amount += CheckAusearch.useraud_event_count(cmds[index], start)

        # сравниваем полученные результаты
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

        # удаляем пользователя
        for index in range(count):
            name = user + str(index)
            User.rm_priv(name)
            User.rm(name)

        # возвращаем результат
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
        Prepare.clean()

        manager = Manager()
        timers = manager.list([None])

        test_ps = self._create_ps(syscall=audit_flag,
                                  func=self._template_ps_fileaud_timer,
                                  proc_lifetime=ps_lifetime,
                                  delay=event_re_initialization_delay,
                                  timer_lst=timers)
        test_ps.start()

        target_file = 'file_' + audit_flag + '0'
        start, end = time(), time()
        while timers[0] is None:
            pass
        while CheckAusearch.fileaud(target_file, timers[0]) is False:
            end = time()
            if not test_ps.is_alive():
                return 0

        test_ps.terminate()
        Prepare.clean()

        latency = end - start
        if latency < event_re_initialization_delay:
            latency = 0.001
        return round(latency, accuracy)

    def test_get_latency_fileaud_under_load(self,
                                            audit_flag,
                                            count,
                                            ps_lifetime=None,
                                            event_re_initialization_delay=None,
                                            accuracy=3):

        Auditd.clean()
        Prepare.clean()

        if ps_lifetime is None:
            ps_lifetime = count

        manager = Manager()
        timers = manager.list([None] * count)

        killer_ps(stand_number)

        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_fileaud_timer,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count,
                                      timer_lst=timers)
        for test_ps in test_ps_lst:
            test_ps.start()

        last_num = count - 1
        last_test_ps = test_ps_lst[-1]
        target_file = 'file_' + audit_flag + str(count - 1)
        start, end = time(), time()  # засекаем время
        while True:
            try:
                if timers[last_num]:
                    break
            except Exception:
                pass
        while CheckAusearch.fileaud(target_file, timers[last_num]) is False:
            end = time()
            if not last_test_ps.is_alive():
                return 0

        for test_ps in test_ps_lst:
            test_ps.terminate()

        Prepare.clean()

        latency = end - start
        if latency < event_re_initialization_delay:
            latency = 0.001
        return round(latency, accuracy)

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

        manager = Manager()
        counters = manager.list([0]*count)

        # инициализируем процессы
        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_fileaud_counter,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count,
                                      counters_lst=counters)

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
        expected_event_amount = sum(counters)

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
                                                                                          eps=int(int(ps_count) / float(ps_event_re_initialization_delay)),
                                                                                          exp_ev_am=result[1],
                                                                                          aud_ev_am=result[2],
                                                                                          prct_res=result[3],
                                                                                          bool_res=result[0]))
        print('{f} {eps} {exp_ev_am} {aud_ev_am} {prct_res} {bool_res}\n'.format(f=event_flag,
                                                                                 eps=int(int(ps_count) / float(ps_event_re_initialization_delay)),
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
                                                                                          eps=int(int(ps_count) / float(ps_event_re_initialization_delay)),
                                                                                          exp_ev_am=result[1],
                                                                                          aud_ev_am=result[2],
                                                                                          prct_res=result[3],
                                                                                          bool_res=result[0]))
        print('{f} {eps} {exp_ev_am} {aud_ev_am} {prct_res} {bool_res}\n'.format(f=event_flag,
                                                                                 eps=int(int(ps_count) / float(ps_event_re_initialization_delay)),
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
                                                                                          eps=int(int(ps_count) / float(ps_event_re_initialization_delay)),
                                                                                          exp_ev_am=result[1],
                                                                                          aud_ev_am=result[2],
                                                                                          prct_res=result[3],
                                                                                          bool_res=result[0]))
        print('{f} {eps} {exp_ev_am} {aud_ev_am} {prct_res} {bool_res}\n'.format(f=event_flag,
                                                                                 eps=int(int(ps_count) / float(ps_event_re_initialization_delay)),
                                                                                 exp_ev_am=result[1],
                                                                                 aud_ev_am=result[2],
                                                                                 prct_res=result[3],
                                                                                 bool_res=result[0]))
