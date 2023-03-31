import os
import subprocess
import paramiko

from time import sleep
from fabric import Connection
from ipa_conf import USER, PASSWORD, PASSWORD_DOCKER_CONT, HOSTS

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
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy)
    client.connect(hostname=host, username=user, password=passwd, port=port)
    stdin, stdout, stderr = client.exec_command(f'{command}')
    data = stdout.read().decode("utf-8") + stderr.read().decode("utf-8")
    client.close()
    return data

def remote_put_file(host, remote_path, local_path, port=22, user=USER, passwd=PASSWORD):
    transport = paramiko.Transport((host, port))
    transport.connect(username=user, password=passwd)
    sftp = paramiko.SFTPClient.from_transport(transport)
    sftp.put(localpath=local_path, remotepath=remote_path)
    sftp.close()
    transport.close()


def get_cmd_start(cmd, host, ssh_username, ssh_pass, port=22, id_client=0):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=ssh_username, password=ssh_pass, port=port)
    print(id_client, ": get_cmd_start")
    return ssh, ssh.exec_command(cmd)

def get_cmd_out(ssh, stdin, stdout, strerr, id_client=0):
    result =stdout.read()
    out = result.decode('UTF-8')
    ssh.close()
    print(id_client, ": get_cmd_out")
    return out




