import fabric
import paramiko
import subprocess
import os
from invoke import UnexpectedExit


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
