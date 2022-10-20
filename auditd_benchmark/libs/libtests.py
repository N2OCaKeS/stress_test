import re
import subprocess

from os import path, mkdir, listdir
from time import sleep, ctime, time
from multiprocessing import Process
from aub_conf import PROC_BODYS
from libs.libaub import Auditd, CheckAusearch, Prepare, cmd


class AuditdTest(Auditd, CheckAusearch):

    def __init__(self, do_positive_test=True, do_negative_test=True, procs=PROC_BODYS):
        self.__pos = do_positive_test
        self.__neg = do_negative_test
        self.__procs = procs

    def _template_ps(self,
                     syscall,
                     life_time,
                     delay):
        '''
        :param syscall:
        :param life_time:
        :param delay:
        :param proc_bodies:
        :param positive:
        :param negative:
        :return:
        '''
        while life_time > 0:
            sleep(delay)
            if self.__pos:
                with open('/tmp/timer', 'w') as file:
                    file.write(ctime())
                cmd(self.__procs[syscall][0])
            if self.__neg:
                with open('/tmp/timer', 'w') as file:
                    file.write(ctime())
                cmd(self.__procs[syscall][1])
            life_time -= delay

    @staticmethod
    def _create_ps(syscall, func, proc_lifetime, delay):
        '''
        :param syscall:
        :param func:
        :return:
        '''
        process = Process(name='test_process_{}'.format(syscall),
                          target=func,
                          args=(syscall, proc_lifetime, delay))
        process.start()
        return process

    def test_get_latency_auditd(self, audit_flag, ps_lifetime=1, event_re_initialization_delay=0.001, accurancy=3):
        Auditd.clean()
        test_ps = self._create_ps(syscall=audit_flag,
                                  func=self._template_ps,
                                  proc_lifetime=ps_lifetime,
                                  delay=event_re_initialization_delay)
        cmd('psaud {pid} +{flag}:-{flag}'.format(pid=test_ps.pid, flag=(audit_flag)))
        start, end = time(), time()
        while test_ps.is_alive() and CheckAusearch.psaud(audit_flag, test_ps.pid) is False:
            end = time()
        test_ps.join()
        return round(end - start, accurancy)


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

