import os
import re
import crypt
import subprocess
import pexpect
import pdb

from os import path, mkdir, listdir, chmod
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


class CheckAusearch:

    # process audit
    @staticmethod
    def psaud(audit_flag, pid, time_file='/tmp/timer0'):
        try:
            with open(time_file, 'r') as file:
                search_time = file.read().split()[3]
        except (IndexError, FileNotFoundError):
            search_time = ctime().split()[3]

        if audit_flag == 'chroot':
            pid = 'ppid=1'
        if (audit_flag == 'mac') or (audit_flag == 'cap') or (audit_flag == 'acl'):
            ausearch_cmd = 'ausearch -i -ts "{}"'
        else:
            ausearch_cmd = 'ausearch -i -ts "{}" -k parsec-p'

        try:
            au_return = cmd(ausearch_cmd.format(search_time)).stdout.decode('utf-8')
        except UnicodeDecodeError:
            au_return = ''
        return re.search(str(pid), au_return) is not None


    @staticmethod
    def psaud_event_count(audit_flag, pid, search_time):
        search_time = search_time.split()[3]

        if audit_flag == 'chroot':
            pid = 'ppid=1'
        if (audit_flag == 'mac') or (audit_flag == 'cap') or (audit_flag == 'acl'):
            ausearch_cmd = 'ausearch -i -ts "{}"'
        else:
            ausearch_cmd = 'ausearch -i -ts "{}" -k parsec-p'
        try:
            au_return = cmd(ausearch_cmd.format(search_time)).stdout.decode('utf-8')
        except UnicodeDecodeError:
            au_return = ''
        return len(re.findall(str(pid), au_return))


    # user audit
    @staticmethod
    def useraud(user_cmd, time, time_file='/tmp/timer0'):
        # try:
        #     with open(time_file, 'r') as file:
        #         search_time = file.read().split()[3]
        # except (IndexError, FileNotFoundError):
        #     search_time = ctime().split()[3]

        search_time = time.split()[3]
        try:
            au_return = cmd('ausearch -i -ts "{}"'.format(search_time)).stdout.decode('utf-8')
        except UnicodeDecodeError:
            au_return = ''
        return re.search(str(user_cmd), au_return) is not None

    @staticmethod
    def useraud_event_count(pid, search_time):
        search_time = search_time.split()[3]
        try:
            au_return = cmd('ausearch -i -ts "{}"'.format(search_time)).stdout.decode('utf-8')
        except UnicodeDecodeError:
            au_return = ''
        return len(re.findall(str(pid), au_return))

    # file audit
    @staticmethod
    def fileaud(target_file, time_file='/tmp/timer0'):
        try:
            with open(time_file, 'r') as file:
                search_time = file.read().split()[3]
        except (IndexError, FileNotFoundError):
            search_time = ctime().split()[3]
        try:
            au_return = cmd('ausearch -i -ts "{}"'.format(search_time)).stdout.decode('utf-8')
        except UnicodeDecodeError:
            au_return = ''
        return re.search(target_file, au_return) is not None

    @staticmethod
    def fileaud_event_count(target_file, search_time):
        search_time = search_time.split()[3]
        try:
            au_return = cmd('ausearch -i -ts "{}"'.format(search_time)).stdout.decode('utf-8')
        except UnicodeDecodeError:
            au_return = ''
        return len(re.findall(str(target_file), au_return))


class Auditd:

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


class Prepare:

    @staticmethod
    def file(how_many=1, where='/tmp'):
        for num in range(how_many):
            if path.exists('{}/file{}'.format(where, num)) is False:
                file = open('{}/file{}'.format(where, num), 'w')
                file.close()
                chmod('{}/file{}'.format(where, num), 0o777)

    @staticmethod
    def dir(how_many=1, where='/tmp'):
        for num in range(how_many):
            if path.exists('{}/dir{}'.format(where, num)) is False:
                try:
                    mkdir('{}/dir{}'.format(where, num))
                except FileExistsError:
                    sleep(0.1)
                    mkdir('{}/dir{}'.format(where, num))
                chmod('{}/dir{}'.format(where, num), 0o777)

    @staticmethod
    def clean(where='/tmp'):
        if path.getsize(where) != 0:
            try:
                cmd('rm -rf {}/dir*'.format(where))
                cmd('rm -rf {}/file*'.format(where))
                cmd('rm -rf {}/counter*'.format(where))
            except FileExistsError:
                sleep(0.1)
                cmd('rm -rf {}/dir*'.format(where))
                cmd('rm -rf {}/file*'.format(where))
                cmd('rm -rf {}/counter*'.format(where))


class User:

    @staticmethod
    def add(name, password='1'):
        if os.path.exists('/home/'+name):
            User.rm(name)
        encode_passwd = crypt.crypt(password, '22')
        cmd('useradd -p {ep} -d /home/{n} -s /bin/bash -m {n} &> /dev/null'.format(ep=encode_passwd, n=name))
        cmd('echo {}:{} | chpasswd  &> /dev/null'.format(name, password))

    @staticmethod
    def add_priv(name, priv):
        cmd('usercaps -l +{} {}'.format(priv, name))
        child_term = pexpect.spawn('su ' + name)
        child_term.sendline('usercaps ' + name)
        child_term.sendline('exit')

    @staticmethod
    def add_to_group(name, group):
        cmd('gpasswd -a {} {}'.format(name, group))
        child_term = pexpect.spawn('su ' + name)
        child_term.sendline('id ' + name)
        child_term.sendline('exit')

    @staticmethod
    def rm_priv(name):
        cmd('usercaps -d ' + name)
        child_term = pexpect.spawn('su ' + name)
        child_term.sendline('usercaps ' + name)
        child_term.sendline('exit')

    @staticmethod
    def rm(name):
        cmd('userdel -r -Z'+name)


class UnixUser(object):

    def __init__(self, uid, gid=None):
        self.uid = uid
        self.gid = gid

    def __enter__(self):
        self.cache = os.getuid(), os.getgid()
        os.setuid(self.uid)
        if self.gid is not None:
            os.setgid(self.gid)

    def __exit__(self, exc_type, exc_val, exc_tb):
        os.setuid(self.cache[0])
        if self.gid is not None:
            os.setgid(self.cache[1])
