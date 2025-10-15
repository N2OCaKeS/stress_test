import subprocess
import asyncio
from os import linesep
import paramiko
from os.path import exists
from new_astra_openvpn.conf import INFO_FILENAME, JIRA_URL, CONFLUENCE_URL, PACKAGE, sys_cls
import requests
import time


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
            info.write(PACKAGE)


def response():
    try:
        jira = requests.get(f'https://{JIRA_URL}').status_code
        life = requests.get(f'https://{CONFLUENCE_URL}').status_code
        return jira, life
    except Exception as e:
        jira, life = str(type(e).__name__), str(e)
        return jira, life

