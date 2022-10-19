import os
import re
import subprocess
from time import sleep, ctime
from multiprocessing import Process
from aub_conf import PROC_BODYS


def cmd(command,
        err=subprocess.DEVNULL,
        out=subprocess.DEVNULL):
    '''
    :param command:
    :param err:
    :param out:
    :return:
    '''
    subprocess.run(command, shell=True, stderr=err, stdout=out)


def template_ps(syscall,
                life_time,
                delay,
                proc_bodies=PROC_BODYS,
                positive=True,
                negative=True):
    '''
    :param syscall:
    :param life_time:
    :param delay:
    :param proc_bodies:
    :param positive:
    :param negative:
    :return:
    '''
    while life_time:
        sleep(delay)
        if positive:
            with open('/tmp/timer', 'w') as file:
                file.write(ctime())
            cmd(proc_bodies[syscall][0])
        if negative:
            with open('/tmp/timer', 'w') as file:
                file.write(ctime())
            cmd(proc_bodies[syscall][1])
        life_time -= 1


def create_ps(syscall, func=template_ps):
    '''
    :param syscall:
    :param func:
    :return:
    '''
    process = Process(name='test_process_{}'.format(syscall),
                      target=func,
                      args=(syscall, 10, 1))
    process.start()
    return process


class CheckAusearch():

    # process audit
    @staticmethod
    def psaud(audit_flag, pid,):
        # try:
        #     search_time = re.search(r"[0-9]{2}:[0-9]{2}", ts)[0]
        # except TypeError:
        #     ts_lst = ts.split()[3].split(':')
        #     search_time = ts_lst[0]+''+ts_lst[1]
        with open('/tmp/timer', 'r') as file:
            search_time=file.read().split()[3]
        if (audit_flag == 'mac') or (audit_flag == 'cap') or (audit_flag == 'acl'):
            au_return_all = os.popen('ausearch -i -ts "{}"'.format(search_time)).read()
            return re.search(str(pid), au_return_all) is not None
        else:
            au_return_p = os.popen('ausearch -i -ts "{}" -k parsec-p'.format(search_time)).read()
            return re.search(str(pid), au_return_p) is not None

