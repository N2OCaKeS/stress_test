import subprocess
from os import linesep
import paramiko
from os.path import exists
from ovpn_conf import INFO_FILENAME, JIRA_URL, CONFLUENCE_URL
import requests
import time


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


def silens_cmd(command, err=subprocess.DEVNULL, out=subprocess.DEVNULL):
    subprocess.run(command, shell=True, stderr=err, stdout=out)


def cmd(command):
    subprocess.run(command, shell=True)


def trycorator(function):
    def wrapper(*args, **kwargs):
        try: 
            function(*args, **kwargs)
        except Exception as e:
            print(f'Function: {function.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')

    return wrapper


def timer(func):
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        elapsed_time = (end_time - start_time) / 60
        print(f"Затрачено времени: {round(elapsed_time, 4)} мин")
        return result
    return wrapper


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


def response():
    try:
        jira = requests.get(f'https://{JIRA_URL}').status_code
        life = requests.get(f'https://{CONFLUENCE_URL}').status_code
        return jira, life
    except Exception as e:
        jira, life = str(type(e).__name__), str(e)
        return jira, life
