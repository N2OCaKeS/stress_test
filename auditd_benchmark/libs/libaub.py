import os
import re
import subprocess

from os import path, mkdir, listdir
from time import sleep, ctime, time


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


class CheckAusearch():

    # process audit
    @staticmethod
    def psaud(audit_flag, pid, time_file='/tmp/timer'):

        with open(time_file, 'r') as file:
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

    @staticmethod
    def psaud_event_count(audit_flag, search_time, pid):
        search_time = search_time.split()[3]
        if (audit_flag == 'mac') or (audit_flag == 'cap') or (audit_flag == 'acl'):
            au_return_all = cmd('ausearch -i -ts "{}"'.format(search_time)).stdout.decode('utf-8')
            return len(re.findall(str(pid), au_return_all))
        else:
            au_return_p = cmd('ausearch -i -ts "{}" -k parsec-p'.format(search_time)).stdout.decode('utf-8')
            return len(re.findall(str(pid), au_return_p))

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
            cmd('rm -rf {}/counter*'.format(where))
            cmd('rm -rf {}/timer*'.format(where))