import fabric
from fabric import Connection
import paramiko
import subprocess
import os
from os.path import exists
from invoke import UnexpectedExit
import requests
from apa_conf import *


def ssh_shell_command(comm, node):
    try:
        node.run("id", hide=True)
    except fabric.exceptions.GroupException as e:
        for c, r in e.result.items():
            if isinstance(r, paramiko.ssh_exception.NoValidConnectionsError):
                print("Host {} unavailable, check power".format(c.host))
                node.remove(c)
            elif isinstance(r, paramiko.ssh_exception.AuthenticationException):
                print("Auth failed on {} check creds".format(c.host))
                node.remove(c)
    node.run(comm)


def command(command):
    result = subprocess.run([command], shell=True)
    return result.returncode


def check_output_command(command):
    result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    output, errors = result.communicate()
    output = os.linesep.join([s for s in output.splitlines() if s])
    errors = os.linesep.join([s for s in errors.splitlines() if s])
    if errors == "":
        return output
    else:
        return errors


def astra_mode_switch(status, node):
    if status == "enable":
        ssh_shell_command("echo | sudo -S sed -i -e 's/# AstraMode on/AstraMode on/' /etc/apache2/apache2.conf", node)
        ssh_shell_command("echo | sudo -S sed -i -e 's/AstraMode off/AstraMode on/' /etc/apache2/apache2.conf", node)
    elif status == "disable":
        ssh_shell_command("echo | sudo -S sed -i -e 's/# AstraMode on/AstraMode off/' /etc/apache2/apache2.conf", node)
        ssh_shell_command("echo | sudo -S sed -i -e 's/AstraMode on/AstraMode off/' /etc/apache2/apache2.conf", node)

    ssh_shell_command("echo | sudo -S systemctl restart apache2.service", node)


def create_user(login_name, node):
    try:
        ssh_shell_command("sudo yes '1' | sudo adduser {}".format(login_name), node)
    except UnexpectedExit:
        print("Catch error of existent user, ignoring")


def set_level_on_user(user, node, integrity="0", level="0:0", category="0:0"):
    try:
        ssh_shell_command("sudo pdpl-user -i {} -l {} -c {} {}".format(integrity, level, category, user), node)
    except UnexpectedExit:
        print("Catch error of existent user levels, ignoring")


class Color:
    Red = '\033[91m'
    Green = '\033[92m'
    Yellow = '\033[93m'
    Cyan = "\033[1;36m"
    Blue = '\033[94m'
    Bold = '\033[1m'
    END = '\033[0m'
    BLINK = '\33[5m'


def response():
    try:
        jira = requests.get(f'https://{JIRA_URL}').status_code
        life = requests.get(f'https://{CONFLUENCE_URL}').status_code
        return jira, life
    except Exception as e:
        jira, life = str(type(e).__name__), str(e)
        return jira, life


def astra_version():
    version = []
   
    if exists("/etc/astra_version"):
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
    else:
        if exists("/etc/debian_version"):
            with open("/etc/debian_version", "r") as file:
                debian_version = file.read()
            version.append(debian_version.strip('\n'))
            return (version[0], 'orel')
    return version


def info_list():
        if exists(INFO_FILENAME):
            report = open(INFO_FILENAME, 'w')
            report.close()

        info_lst = [f'{astra_version()[0]}({astra_version()[1]})\n',
                    subprocess.run('uname -r',
                                    shell=True,
                                    stdout=subprocess.PIPE).stdout.decode("utf-8")]

        with open(INFO_FILENAME, 'a+') as info:
            info.writelines(info_lst)


class ApacheNode:
    admin = Connection(
            host=TESTED_SERVER_IP, 
            user=TESTED_SERVER_ADMIN_USER, 
            connect_kwargs=TESTED_SERVER_ADMIN_CREDS, 
            port=TESTED_SERVER_SSH_PORT)
    
    qa0_login = Connection(host=TESTED_SERVER_IP,
                           user=TESTED_QA_USER,
                           connect_kwargs=TESTED_SERVER_QA_CREDS,
                           port=TESTED_SERVER_SSH_PORT)


    qa1_login = Connection(host=TESTED_SERVER_IP,
                           user=TESTED_QA_USER_MAC,
                           connect_kwargs=TESTED_SERVER_QA_CREDS,
                           port=TESTED_SERVER_SSH_PORT)

    qa2_login = Connection(host=TESTED_SERVER_IP,
                           user=TESTED_QA_USER_MAC_CAT,
                           connect_kwargs=TESTED_SERVER_QA_CREDS,
                           port=TESTED_SERVER_SSH_PORT)

class ClientNode:
    admin = Connection(
            host=CLIENT_IP, 
            user=CLIENT_ADMIN_USER, 
            connect_kwargs=CLIENT_ADMIN_CREDS, 
            port=CLIENT_SSH_PORT)
    
    qa0_login = Connection(host=CLIENT_IP,
                           user=TESTED_QA_USER,
                           connect_kwargs=TESTED_SERVER_QA_CREDS,
                           port=CLIENT_SSH_PORT)


    qa1_login = Connection(host=CLIENT_IP,
                           user=TESTED_QA_USER_MAC,
                           connect_kwargs=TESTED_SERVER_QA_CREDS,
                           port=CLIENT_SSH_PORT)

    qa2_login = Connection(host=CLIENT_IP,
                           user=TESTED_QA_USER_MAC_CAT,
                           connect_kwargs=TESTED_SERVER_QA_CREDS,
                           port=CLIENT_SSH_PORT)