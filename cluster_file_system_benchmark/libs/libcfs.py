# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import paramiko
import subprocess
from time import strftime, time, gmtime
from os import linesep

def create_remote_file(local_file_path, remote_file_path, ip, user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=ip, username=user, password=password, port=22)
    ftp = client.open_sftp()
    files = ftp.put(local_file_path, remote_file_path)
    ftp.close()
    client.close()


def send_remote_command(command, ip, user, password):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=ip, username=user, password=password, port=22)
    ssh.get_transport().set_keepalive(60)
    chanel = ssh.get_transport().open_session()
    chanel.get_pty()
    chanel.exec_command(command)
    output = chanel.makefile().read().decode('utf-8')
    err_output = chanel.makefile_stderr().read().decode('utf-8')
    if output != '':
        print(f'STDOUT:\n{output}')
    if err_output != '':
        print(f'STDERR:\n{err_output}')
    ssh.close()


def get_remote_file(remote_file_path, local_file_path, ip, user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=ip, username=user, password=password, port=22)
    ftp = client.open_sftp()
    files = ftp.get (remote_file_path, local_file_path)
    ftp.close()
    client.close()

def astra_version():
    version = []
    with open("/etc/astra_version", "r") as file:
        astra_update_version = file.read()
    version.append(astra_update_version.strip('\n'))

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

def check_output_command(command, out=None):
    result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    output, errors = result.communicate()
    output = linesep.join([s for s in output.splitlines() if s])
    errors = linesep.join([s for s in errors.splitlines() if s])
    if errors == "":
        return output
    elif out != None:
        return errors + output
    else:
        return errors


def put_system_info_in_file(start, file):
    lead_time = strftime("%H:%M:%S", gmtime(time() - start))
    print('lead time: {t}'.format(t=lead_time))

    # собрать системную информацию
    info_lst = ['{digit_v}({mode})\n'.format(digit_v=astra_version()[0], mode=astra_version()[1]),
                subprocess.run('uname -r',
                               shell=True,
                               stdout=subprocess.PIPE).stdout.decode("utf-8"),
                subprocess.run("dpkg -l ocfs2-tools | awk '{print $3}' | tail -n1",
                               shell=True,
                               stdout=subprocess.PIPE).stdout.decode("utf-8"),
                str(lead_time)]

    with open(file, 'a+') as info:
        info.writelines(info_lst)
