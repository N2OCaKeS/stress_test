import re
import subprocess

from os import path, mkdir, listdir
from pathlib import Path
from time import sleep, ctime, time
from multiprocessing import Process
from aub_conf import PROC_BODYS
from libs.libaub import Auditd, CheckAusearch, Prepare, cmd


class AuditdTest(Auditd, CheckAusearch):

    def __init__(self, do_positive_test=True, do_negative_test=True, procs=PROC_BODYS):
        self.__pos = do_positive_test
        self.__neg = do_negative_test
        self.__procs = procs

    def _template_ps_timer(self,
                           syscall,
                           life_time,
                           delay,
                           number=None):

        if number is None:
            timer_file = '/tmp/timer'
        else:
            timer_file = '/tmp/timer' + str(number)

        while life_time > 0:
            sleep(delay)
            if self.__pos:
                with open(timer_file, 'w') as file:
                    file.write(ctime())
                cmd(self.__procs[syscall][0])
            # if self.__neg:
            #     with open(timer_file, 'w') as file:
            #         file.write(ctime())
            #     cmd(self.__procs[syscall][1])
            life_time -= delay

    def _template_ps_counter(self,
                             syscall,
                             life_time,
                             delay,
                             number=None):

        if number is None:
            counter_file = '/tmp/counter'
        else:
            counter_file = '/tmp/counter' + str(number)

        counter = 0
        while life_time > 0:
            sleep(delay)
            if self.__pos:
                cmd(self.__procs[syscall][0])
                counter += 1
                with open(counter_file, 'w') as file:
                    file.write(str(counter))
            # if self.__neg:
            #     cmd(self.__procs[syscall][1])
            #     counter += 1
            #     with open(counter_file, 'a+') as file:
            #         file.write(str(counter))
            life_time -= delay
        print('сгенерировано '+ str(counter) + ' событий')

    @staticmethod
    def _create_ps(syscall,
                   func,
                   proc_lifetime,
                   delay,
                   count=None):

        print('{} event / sec'.format(int(float(proc_lifetime) / float(delay) * int(count))))

        if count is None:
            process = Process(name='test_process_{}'.format(syscall),
                              target=func,
                              args=(syscall, proc_lifetime, delay))
            process.start()
            return process
        else:
            processes_lst = []
            for index in range(int(count)):
                processes_lst.append(Process(name='test_process_{}_{}'.format(syscall, index),
                                             target=func,
                                             args=(syscall, proc_lifetime, delay, index)))
                processes_lst[index].start()
            return processes_lst

    def test_get_latency_auditd(self,
                                audit_flag,
                                ps_lifetime=1,
                                event_re_initialization_delay=0.001,
                                accurancy=3):
        Auditd.clean()
        test_ps = self._create_ps(syscall=audit_flag,
                                  func=self._template_ps_timer,
                                  proc_lifetime=ps_lifetime,
                                  delay=event_re_initialization_delay)
        cmd('psaud {pid} +{flag}:-{flag}'.format(pid=test_ps.pid, flag=(audit_flag)))
        start, end = time(), time()
        while test_ps.is_alive() and CheckAusearch.psaud(audit_flag, test_ps.pid) is False:
            end = time()
        test_ps.join()
        return round(end - start, accurancy)

    def test_get_latency_auditd_under_load(self,
                                           audit_flag,
                                           count,
                                           ps_lifetime=None,
                                           event_re_initialization_delay=0.001,
                                           accurancy=3):

        Auditd.clean()
        if ps_lifetime is None:
            ps_lifetime = count
        test_ps_lst = self._create_ps(syscall=audit_flag,
                                      func=self._template_ps_timer,
                                      proc_lifetime=ps_lifetime,
                                      delay=event_re_initialization_delay,
                                      count=int(count))
        for test_ps in test_ps_lst:
            cmd('psaud {pid} +{flag}:-{flag}'.format(pid=str(test_ps.pid), flag=(audit_flag)))

        last_test_ps = test_ps_lst[-1]
        start, end = time(), time()
        while last_test_ps.is_alive() and CheckAusearch.psaud(audit_flag, last_test_ps.pid) is False:
            end = time()

        for test_ps in test_ps_lst:
            test_ps.join()

        return round(end - start, accurancy)

    def test_losses_auditd_under_load(self,
                                      audit_flag,
                                      count,
                                      ps_lifetime=None,
                                      event_re_initialization_delay=0.001):
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
            cmd('psaud {pid} +{flag}:-{flag}'.format(pid=str(test_ps.pid), flag=(audit_flag)))

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

        # считаем суммарное количество событий замеченных auditd
        auditd_events_amount = 0
        for test_ps in test_ps_lst:
            r = CheckAusearch.psaud_event_count(audit_flag, start, test_ps.pid)
            print('получено auditd '+str(r)+' событий')
            auditd_events_amount += r

        # ghjwtynyj
        if auditd_events_amount / expected_event_amount > 1:
            res = 100.0
        else:
            res = round(auditd_events_amount / expected_event_amount, 2) * 100

        return [expected_event_amount <= auditd_events_amount,
                expected_event_amount,
                auditd_events_amount,
                res]


class AuditdTestSet():

    @staticmethod
    def get_latency_auditd_single(event_flag, report_file):
        __audit_test = AuditdTest()
        result = __audit_test.test_get_latency_auditd(event_flag)
        with open(report_file, 'w') as file:
            file.write('{} - {} sec\n'.format(event_flag, result))
        print('{} - {} sec'.format(event_flag, result))

    @staticmethod
    def get_latency_auditd_total(event_flag_lst, report_file):
        __audit_test = AuditdTest()
        for event_flag in event_flag_lst:

            if event_flag in ['open', 'remove', 'chmod', 'chown', 'cap', 'rename']:
                Prepare.file()
            elif event_flag in ['acl', 'mac', 'mount']:
                Prepare.dir()
            else:
                pass

            result = __audit_test.test_get_latency_auditd(event_flag)

            if event_flag in ['open', 'remove', 'chmod', 'chown', 'acl', 'mac', 'mount', 'cap', 'rename']:
                Prepare.clean()
            else:
                pass

            with open(report_file, 'a+') as file:
                file.write('{} - {} sec\n'.format(event_flag, result))
            print('{} - {} sec'.format(event_flag, result))

    @staticmethod
    def get_latency_auditd_single_stress(event_flag,
                                         file_count,
                                         report_file):
        __audit_test = AuditdTest()
        result = __audit_test.test_get_latency_auditd_under_load(event_flag, int(file_count))
        with open(report_file, 'w') as file:
            file.write('{} - {} sec\n'.format(event_flag, result))
        print('{} - {} sec'.format(event_flag, result))

    @staticmethod
    def get_losses_auditd_single_stress(event_flag,
                                        ps_count,
                                        ps_lifetime,
                                        ps_event_re_initialization_delay,
                                        report_file):
        __audit_test = AuditdTest()
        result = __audit_test.test_losses_auditd_under_load(event_flag,
                                                            ps_count,
                                                            ps_lifetime,
                                                            ps_event_re_initialization_delay)
        Prepare.clean()
        with open(report_file, 'w') as file:
            file.write('{f} {exp_ev_am} {aud_ev_am} {prct_res} {bool_res}\n'.format(f=event_flag,
                                                                                    exp_ev_am=result[1],
                                                                                    aud_ev_am=result[2],
                                                                                    prct_res=result[3],
                                                                                    bool_res=result[0]))
        print('{f} {exp_ev_am} {aud_ev_am} {prct_res} {bool_res}\n'.format(f=event_flag,
                                                                                    exp_ev_am=result[1],
                                                                                    aud_ev_am=result[2],
                                                                                    prct_res=result[3],
                                                                                    bool_res=result[0]))