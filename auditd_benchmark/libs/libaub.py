import re
import subprocess

from os import path, mkdir, listdir
from time import sleep, ctime, time
from multiprocessing import Process
from aub_conf import PROC_BODYS


def cmd(command,
        err=subprocess.DEVNULL,
        out=subprocess.PIPE):
    '''
    :param command:
    :param err:
    :param out:
    :return:
    '''
    return subprocess.run(command, shell=True, stderr=err, stdout=out)


def template_ps(syscall,
                life_time,
                delay,
                positive=True,
                negative=True,
                proc_bodies=PROC_BODYS):
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
        if positive:
            with open('/tmp/timer', 'w') as file:
                file.write(ctime())
            cmd(proc_bodies[syscall][0])
        if negative:
            with open('/tmp/timer', 'w') as file:
                file.write(ctime())
            cmd(proc_bodies[syscall][1])
        life_time -= delay


def create_ps(syscall, func=template_ps):
    '''
    :param syscall:
    :param func:
    :return:
    '''
    process = Process(name='test_process_{}'.format(syscall),
                      target=func,
                      args=(syscall, 1, 0.001, True, False))
    process.start()
    return process


def test_get_latency_auditd(audit_flag, accurancy=3):
    Auditd.clean()
    test_ps = create_ps(audit_flag)
    cmd('psaud {pid} +{flag}:-{flag}'.format(pid=test_ps.pid, flag=(audit_flag)))
    start, end = time(), time()
    while test_ps.is_alive() and CheckAusearch.psaud(audit_flag, test_ps.pid) is False:
        end = time()
    test_ps.join()
    return '{} {}'.format(audit_flag, round(end-start, accurancy))


class CheckAusearch():

    # process audit
    @staticmethod
    def psaud(audit_flag, pid):

        with open('/tmp/timer', 'r') as file:
            try:
                search_time = file.read().split()[3]
            except IndexError:
                search_time = ctime().split()[3]

        if (audit_flag == 'mac') or (audit_flag == 'cap') or (audit_flag == 'acl'):
            au_return_all = cmd('ausearch -i -ts "{}"'.format(search_time)).stdout.decode('utf-8')
            return re.search(str(pid), au_return_all) is not None
        else:
            au_return_p = cmd('ausearch -i -ts "{}" -k parsec-p'.format(search_time)).stdout.decode('utf-8')
            return re.search(str(pid), au_return_p) is not None

    # process audit
    @staticmethod
    def useraud(audit_flag, pid):
        pass

    # process audit
    @staticmethod
    def setfaud(audit_flag, pid):
        pass


class Auditd():

    @staticmethod
    def check_status():
        return cmd('sudo systemctl status auditd').returncode == 0

    @staticmethod
    def clean():
        cmd('service auditd rotate')
        files = listdir('/var/log/audit')
        for audit_file in files:
            f = open('/var/log/audit/{}'.format(audit_file), 'w')
            f.close()


class Prepare():

    @staticmethod
    def file(how_many=1, where='/tmp'):
        for num in range(1, how_many+1):
            if path.exists('{}/file{}'.format(where, num)) is False:
                cmd('touch {}/file{}'.format(where, num))

    @staticmethod
    def dir(how_many=1, where='/tmp'):
        for num in range(1, how_many+1):
            if path.exists('{}/dir{}'.format(where, num)) is False:
                mkdir('{}/dir{}'.format(where, num))

    @staticmethod
    def clean(where='/tmp'):
        if path.getsize(where) != 0:
            cmd('rm -rf {}/dir*'.format(where))
            cmd('rm -rf {}/file*'.format(where))