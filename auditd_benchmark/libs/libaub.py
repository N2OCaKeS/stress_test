import os
import re
import subprocess

from os import path, mkdir, listdir
from time import sleep, ctime, time


def cmd(command,
        err=subprocess.DEVNULL,
        out=subprocess.PIPE):
    return subprocess.run(command, shell=True, stderr=err, stdout=out)


def check_output_command(command):
    result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)
    output, errors = result.communicate()
    output = os.linesep.join([s for s in output.splitlines() if s])
    errors = os.linesep.join([s for s in errors.splitlines() if s])
    if errors == "":
        return output
    else:
        return errors


def astra_kernel_version():
    return check_output_command('uname -r')


def astra_version():
    version = []
    with open("/etc/astra_version", "r") as file:
        astra_update_version = file.read()
    version.append(astra_update_version)

    try:
        with open("/etc/astra_license", "r") as file:
            astra_license = file.read()
            if "orel" in astra_license:
                version.append("orel")
            elif "smolensk" in astra_license:
                version.append("smolensk")
            elif "voronezh" in astra_license:
                version.append("voronezh")
            else:
                print("Version of distribution not found")
                exit(2)
    except IOError:
        with open("/etc/astra_version", "r") as file:
            astra_version = file.read()
            if "1.6" in astra_version:
                version.append("smolensk")
            elif "1.5" in astra_version:
                version.append("smolensk")
            elif "8.1" in astra_version:
                version.append("smolensk")
            elif "2.12" in astra_version:
                version.append("orel")
            else:
                print("Version of distribution not found")
                exit(2)

    return version


class CheckAusearch():

    # process audit
    @staticmethod
    def psaud(audit_flag, pid, time_file='/tmp/timer'):

        try:
            with open(time_file, 'r') as file:
                search_time = file.read().split()[3]
        except (IndexError, FileNotFoundError):
            search_time = ctime().split()[3]

        if (audit_flag == 'mac') or (audit_flag == 'cap') or (audit_flag == 'acl'):
            au_return_all = cmd('ausearch -i -ts "{}"'.format(search_time)).stdout.decode('utf-8')
            return re.search(str(pid), au_return_all) is not None
        else:
            au_return_p = cmd('ausearch -i -ts "{}" -k parsec-p'.format(search_time)).stdout.decode('utf-8')
            return re.search(str(pid), au_return_p) is not None

    @staticmethod
    def psaud_event_count(audit_flag, pid, search_time):
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
        for audit_file in listdir('/var/log/audit'):
            f = open('/var/log/audit/{}'.format(audit_file), 'w')
            f.close()
        cmd('service auditd restart')


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