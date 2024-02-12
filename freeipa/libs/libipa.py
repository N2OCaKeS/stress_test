import os
import subprocess
import paramiko
import requests
from datetime import datetime
from paramiko.ssh_exception import NoValidConnectionsError
from time import sleep
from fabric import Connection
from ipa_conf import USER, PASSWORD, HOSTS
import ftplib

def astra_version():
    version = []
    astra_digit_version = subprocess.run("cat /etc/os-release | grep '^VERSION_ID'",
                                         shell=True,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL).stdout.decode("utf-8")
    if "2.12" in astra_digit_version:
        version.append("2.12")
    elif "1.6" in astra_digit_version:
        version.append("1.6")
    elif "1.7" in astra_digit_version:
        version.append("1.7")
    elif "1.8" in astra_digit_version:
        version.append("1.8")
    elif "4.7" in astra_digit_version:
        version.append("4.7")
    elif "8.1" in astra_digit_version:
        version.append("8.1")
    else:
        print("Version of distribution not found")
        #exit(2)
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
                #exit(2)
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
                #exit(2)
    
    astra_full_digit_version = subprocess.run("cat /etc/astra_version",
                                         shell=True,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL).stdout.decode("utf-8")

    astra_kernel_version = subprocess.run("uname -r",
                                         shell=True,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL).stdout.decode("utf-8")
    
    version.append(astra_full_digit_version[:-1])
    version.append(astra_kernel_version[:-1])

    return version

def cmd(command):
    ret_code = subprocess.run(command, shell=True).returncode
    return ret_code

def host_is_available(node):
    try:
        with Connection(host=HOSTS[node]['ip'],
                        user=USER,
                        connect_kwargs={"password": PASSWORD}) as node_client:
            if str(node_client.run('uptime')):
                return True
    except Exception:
        return False

def remote_exec(command, node="server"):
    while host_is_available(node) == False:
        sleep(1)
        print("{host} временно не доступен...повторная попытка подключения...".format(host=HOSTS[node]['ip']))
    try:
        with Connection(host=HOSTS[node]['ip'],
                            user=USER,
                            connect_kwargs={"password": PASSWORD}) as node_client:
            return str(node_client.run(command))
    except Exception as err:
        return "Что то пошло не так...{message}".format(message=err)

def remote_cmd(command, host, user=USER, passwd=PASSWORD , port=22):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy)
        client.connect(hostname=host, username=user, password=passwd, port=port)
        stdin, stdout, stderr = client.exec_command(f'{command}')
        data = stdout.read().decode("utf-8") + stderr.read().decode("utf-8")
        client.close()
    except paramiko.SSHException as err:
        pass
    return data

def remote_put_file(host, remote_path, local_path, port=22, user=USER, passwd=PASSWORD, local_to_remote=True):
    transport = paramiko.Transport((host, port))
    transport.connect(username=user, password=passwd)
    sftp = paramiko.SFTPClient.from_transport(transport)
    if local_to_remote:
        sftp.put(localpath=local_path, remotepath=remote_path)
    else:
        sftp.get(localpath=local_path, remotepath=remote_path)
    sftp.close()
    transport.close()


def get_cmd_start(cmd, host, ssh_username, ssh_pass, port=22):
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, username=ssh_username, password=ssh_pass, port=port)
    except NoValidConnectionsError:
        return ssh, ["", "Была ошибка NoValidConnectionsError", ""]
    # print(id_client, ": get_cmd_start")
    return ssh, ssh.exec_command(cmd)

def get_cmd_out(ssh, stdin, stdout, strerr):
    try:
        result = stdout.read()
        out = result.decode('UTF-8')
    except AttributeError:
        out = stdout
    ssh.close()
    # print(id_client, ": get_cmd_out")
    return out

def put_system_info_in_file(start, file):
    def get_duration(duration):
        hours = int(duration / 3600)
        minutes = int(duration % 3600 / 60)
        seconds = int((duration % 3600) % 60)
        return '{:02d}:{:02d}:{:02d}'.format(hours, minutes, seconds)
    
    lead_time = get_duration((datetime.now() - start).total_seconds())
    print('lead time: {t}'.format(t=lead_time))

    # собрать системную информацию
    info_lst = ['{digit_v}({mode})\n'.format(digit_v=remote_cmd('cat /etc/astra_version', HOSTS['server']['ip'])
                                        .strip("\n")
                                        .replace("\x01","")
                                        .replace("(", "")
                                        .replace(")", ""), 
                                    mode=remote_cmd("cat /etc/astra_license | grep DESCRIPTION | sed -n -e 's/^.*(\\(.*\\)).*$/\1/p'", HOSTS['server']['ip'])
                                        .replace("\x01","")
                                        .replace("(", "")
                                        .replace(")", "")),
                                    remote_cmd('uname -r', HOSTS['server']['ip']).replace("\x01","").strip() + '\n',
                                    remote_cmd("dpkg -l astra-freeipa-server | awk '{print $3}' | tail -n1", HOSTS['server']['ip']).replace("\x01","").strip() + '\n',
                                    str(lead_time)]

    with open(file, 'a+') as info:
        info.writelines(info_lst)


def response():
    try:
        jira = requests.get('https://jira.astralinux.ru').status_code
        life = requests.get('https://life.astralinux.ru').status_code
        return jira, life
    except Exception as e:
        jira, life = str(type(e).__name__), str(e)
        return jira, life
    

def upload_results_to_ftp(rc_name, path_to_file, file_name):
    ftp = ftplib.FTP('10.177.103.10')
    ftp.login()
    ftp.cwd('stress_test')
    try:
        ftp.mkd(rc_name)
    except ftplib.error_perm:
        pass
    #ftp.sendcmd('SITE CHMOD 777 ' + rc_name)
    ftp.cwd(rc_name)
    with open(path_to_file, 'rb') as rf:
        ftp.storbinary('STOR ' + file_name, rf)
    ftp.quit()