import os
import pwd
from sys import exit
from pathlib import Path
from time import sleep, ctime, time
from multiprocessing import Process
from aub_conf import PSAUD_PROC_BODYS, TEST_USER
from libs.libaub import Auditd, CheckAusearch, Prepare, User, UnixUser, cmd


class AuditdTest(Auditd, CheckAusearch):
    def __init__(self, do_positive_test=True, do_negative_test=False, procs=PSAUD_PROC_BODYS):
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

    def _pos_files_prep(self, event_flag):
        if event_flag in ['open', 'remove', 'chmod', 'chown', 'rename']:
            Prepare.file()
            return None
        if event_flag in ['acl', 'mount']:
            Prepare.dir()
            return None
        if event_flag in ['mac']:
            Prepare.dir(where='/')
            return None

    def _pos_clean(self, event_flag):
        if event_flag == 'mount':
            cmd('umount /mnt &> /dev/null')
            return None
        if event_flag == 'module':
            cmd(PSAUD_PROC_BODYS[event_flag][0] + ' -r')
            return None
        if event_flag in ['open', 'chmod', 'chown', 'acl', 'mac', 'mount', 'cap', 'rename']:
            Prepare.clean()
            return None

    def _neg_files_prep(self, event_flag):
        pass

    def _neg_clean(self, event_flag):
        pass

    def _template_ps_timer(self,
                           syscall,
                           life_time,
                           delay,
                           number=None):
        '''
        :param syscall: наименование события audit
        :param life_time: время жизни процесса
        :param delay: периодичность генерации события audit
        :param number: количество процессов
        :return:
        '''
        if number is None:
            timer_file = '/tmp/timer'
        else:
            timer_file = '/tmp/timer' + str(number)
        while life_time > 0:
            sleep(delay)
            if self.__pos:
                with open(timer_file, 'w') as file:
                    file.write(ctime())
                self._pos_files_prep(syscall)
                cmd(self.__procs[syscall][0])
                self._pos_clean(syscall)
            if self.__neg:
                with open(timer_file, 'w') as file:
                    file.write(ctime())
                cmd(self.__procs[syscall][1])
            life_time -= delay

    def _template_ps_counter(self,
                             syscall,
                             life_time,
                             delay,
                             number=None):
        '''
        :param syscall: наименование события audit
        :param life_time: время жизни процесса
        :param delay: периодичность генерации события audit
        :param number: количество процессов
        :return:
        '''
        if number is None:
            counter_file = '/tmp/counter'
        else:
            counter_file = '/tmp/counter' + str(number)

        counter = 0
        while life_time > 0:
            sleep(delay)
            if self.__pos:
                self._pos_files_prep(syscall)
                cmd(self.__procs[syscall][0])
                self._pos_clean(syscall)
                counter += 1
                with open(counter_file, 'w') as file:
                    file.write(str(counter))
            if self.__neg:
                cmd(self.__procs[syscall][1])
                counter += 1
                with open(counter_file, 'w') as file:
                    file.write(str(counter))
            life_time -= delay

    @staticmethod
    def _create_ps(syscall,
                   func,
                   proc_lifetime,
                   delay,
                   count=None):
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
                              args=(syscall, proc_lifetime, delay))
            return process
        else:
            print('{} event / sec'.format(int(float(proc_lifetime) / float(delay) * int(count))))
            processes_lst = []
            for index in range(count):
                processes_lst.append(Process(name='test_process_{}_{}'.format(syscall, index),
                                             target=func,
                                             args=(syscall, proc_lifetime, delay, index)))
                # processes_lst[index].start()
            return processes_lst

    def test_get_latency_auditd_psaud(self,
                                      audit_flag,
                                      ps_lifetime=1,
                                      event_re_initialization_delay=0.0001,
                                      accurancy=3):
        '''
        :param audit_flag: наименование события audit
        :param ps_lifetime: время жизни процесса
        :param event_re_initialization_delay: периодичность генерации события audit
        :param accurancy: порядок округления результатов
        :return:
        '''
        Auditd.clean()
        test_ps = self._create_ps(syscall=audit_flag,
                                  func=self._template_ps_timer,
                                  proc_lifetime=ps_lifetime,
                                  delay=event_re_initialization_delay)
        test_ps.start()
        cmd('psaud {pid} +{flag}:-{flag}'.format(pid=test_ps.pid, flag=(audit_flag)))
        start, end = time(), time()
        while test_ps.is_alive() and CheckAusearch.psaud(audit_flag, test_ps.pid) is False:
            end = time()
        test_ps.join()
        return round(end - start, accurancy)

    def test_get_latency_auditd_useraud(self,
                                        audit_flag,
                                        ps_lifetime=1,
                                        event_re_initialization_delay=0.0001,
                                        accurancy=3,
                                        user=TEST_USER):
        '''
        :param audit_flag: наименование события audit
        :param ps_lifetime: время жизни процесса
        :param event_re_initialization_delay: периодичность генерации события audit
        :param accurancy: порядок округления результатов
        :return:
        '''
        Auditd.clean()
        User.add(user)
        with UnixUser(uid=pwd.getpwnam(user).pw_uid):
            test_ps = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_timer,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay)
            test_ps.start()

        cmd('useraud -m {u} +{flag}:-{flag}'.format(u=user, flag=(audit_flag)))
        start, end = time(), time()
        while test_ps.is_alive() and CheckAusearch.useraud(audit_flag, test_ps.pid) is False:
            end = time()

        test_ps.join()
        User.rm(user)
        return round(end - start, accurancy)

    def test_get_latency_auditd_psaud_under_load(self,
                                           audit_flag,
                                           count,
                                           ps_lifetime=None,
                                           event_re_initialization_delay=0.001,
                                           accurancy=3):
        '''
        :param audit_flag: наименование события audit
        :param count: количество процессов
        :param ps_lifetime: время жизни процесса
        :param event_re_initialization_delay: периодичность генерации события audit
        :param accuranc1001y: порядок округления результатов
        :return:
        '''

        Auditd.clean()
        if ps_lifetime is None:
            ps_lifetime = count
        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_timer,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count)
        for test_ps in test_ps_lst:
            test_ps.start()
            cmd('psaud {pid} +{flag}:-{flag}'.format(pid=str(test_ps.pid), flag=(audit_flag)))

        last_test_ps = test_ps_lst[-1]
        last_timefile = '/tmp/timer' + str(count - 1)
        start, end = time(), time()
        while last_test_ps.is_alive() and CheckAusearch.psaud(audit_flag, last_test_ps.pid, last_timefile) is False:
            end = time()

        for test_ps in test_ps_lst:
            test_ps.join()

        return round(end - start, accurancy)

    def test_losses_auditd_under_load(self,
                                      audit_flag,
                                      count,
                                      ps_lifetime=None,
                                      event_re_initialization_delay=0.001):
        '''
        :param audit_flag: наименование события audit
        :param count: количество процессов
        :param ps_lifetime: время жизни процесса
        :param event_re_initialization_delay: периодичность генерации события audit
        :return:
        '''
        Auditd.clean()
        if ps_lifetime is None:
            ps_lifetime = count

        # инициализируем процессы
        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_counter,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=count)

        # точка остчета времени
        start = ctime()

        # навешиваем аудит
        for test_ps in test_ps_lst:
            test_ps.start()
            cmd('psaud {pid} +{flag}:-{flag}'.format(pid=str(test_ps.pid), flag=(audit_flag)))
            # print('запустить процесс ' + str(test_ps.pid))

        # ждем смерть последнего процесса
        last_test_ps = test_ps_lst[-1]
        while last_test_ps.is_alive():
            pass

        # контрольно убиваем процессы
        for test_ps in test_ps_lst:
            test_ps.join()

        # считаем предполагаемое количество событий
        expected_event_amount = 0
        for file in Path('/tmp').glob('counter*'):
            with open(file, 'r') as f:
                expected_event_amount += int(f.read())
                os.remove(file)

        # считаем суммарное количество событий замеченных auditd
        auditd_events_amount = 0
        for test_ps in test_ps_lst:
            auditd_events_amount += CheckAusearch.psaud_event_count(audit_flag, test_ps.pid, start)

        #
        if auditd_events_amount / expected_event_amount > 1:
            res = 100.0
        else:
            res = round(round(auditd_events_amount / expected_event_amount, 2) * 100, 2)

        return [expected_event_amount <= auditd_events_amount,
                expected_event_amount,
                auditd_events_amount,
                res]


class AuditdTestSet():

    @staticmethod
    def clean(event_flag):
        if event_flag == 'mount':
            cmd('umount /mnt &> /dev/null')
        if event_flag == 'module':
            cmd(PSAUD_PROC_BODYS[event_flag][0] + ' -r')
        if event_flag in ['open', 'remove', 'chmod', 'chown', 'acl', 'mac', 'mount', 'cap', 'rename']:
            Prepare.clean()
        Auditd.clean()

    @staticmethod
    def get_latency_psaud_single(event_flag, report_file):
        __audit_test = AuditdTest()
        result = __audit_test.test_get_latency_auditd_psaud(event_flag)
        with open(report_file, 'w') as file:
            file.write('{} - {} sec\n'.format(event_flag, result))
        print('{} - {} sec'.format(event_flag, result))

    @staticmethod
    def get_latency_useraud_single(event_flag, report_file):
        __audit_test = AuditdTest()

        result = __audit_test.test_get_latency_auditd_useraud(audit_flag=event_flag,
                                                              user=TEST_USER)
        with open(report_file, 'w') as file:
            file.write('{} - {} sec\n'.format(event_flag, result))
        print('{} - {} sec'.format(event_flag, result))


    @staticmethod
    def get_latency_auditd_total(event_flag_lst, report_file):
        __audit_test = AuditdTest()
        for event_flag in event_flag_lst:

            result = __audit_test.test_get_latency_auditd_psaud(event_flag)
            AuditdTestSet.clean(event_flag)

            with open(report_file, 'a+') as file:
                file.write('{} - {} sec\n'.format(event_flag, result))
            print('{} - {} sec'.format(event_flag, result))

    @staticmethod
    def get_latency_stat_psaud(event_flag,
                               ps_count,
                               ps_lifetime,
                               ps_event_re_initialization_delay,
                               report_file):

        __audit_test = AuditdTest()
        result = __audit_test.test_get_latency_auditd_psaud_under_load(event_flag,
                                                                 ps_count,
                                                                 ps_lifetime,
                                                                 ps_event_re_initialization_delay)
        AuditdTestSet.clean(event_flag)
        with open(report_file, 'a+') as file:
            file.write('{} {}\n'.format(event_flag, result))
        print('{} - {} sec'.format(event_flag, result))

    @staticmethod
    def get_losses_stat_psaud(event_flag,
                              ps_count,
                              ps_lifetime,
                              ps_event_re_initialization_delay,
                              report_file):

        __audit_test = AuditdTest()
        result = __audit_test.test_losses_auditd_under_load(event_flag,
                                                            ps_count,
                                                            ps_lifetime,
                                                            ps_event_re_initialization_delay)
        AuditdTestSet.clean(event_flag)
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