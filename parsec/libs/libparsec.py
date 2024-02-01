import subprocess
import os
import requests


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
    

def response():
    try:
        jira = requests.get('https://jira.astralinux.ru').status_code
        life = requests.get('https://life.astralinux.ru').status_code
        return jira, life
    except Exception as e:
        jira, life = str(type(e).__name__), str(e)
        return jira, life

