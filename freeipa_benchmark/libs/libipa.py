import os
import subprocess

from time import sleep
from fabric import Connection
from ipa_conf import USER, PASSWORD, HOSTS

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

