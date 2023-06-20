# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: ivelikanov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import subprocess
from datetime import datetime
import ftplib
import requests


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
    elif "4.7" in astra_digit_version:
        version.append("4.7")
    elif "8.1" in astra_digit_version:
        version.append("8.1")
    else:
        print("Version of distribution not found")
        exit(2)
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

def check_service_status(service_name):
    status = subprocess.run("systemctl status {} | grep 'running'".format(service_name),
                                         shell=True,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL).stdout.decode("utf-8")
    if status != "":
        return True
    else:
        return False

def get_memory_load_by_syslog():
    qty_memory = subprocess.run("systemctl status syslog-ng | grep 'Memory'",
                                         shell=True,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL).stdout.decode("utf-8")
    if qty_memory != "":
        qty_memory = float(qty_memory.replace(" ", "")[:-2].split(":")[1]) * 1024
        with open('/proc/meminfo', 'r') as procfile:
            mem_total = int(procfile.readline().replace(" ", "")[:-3].split(":")[1])
            load_syslog_memory = round(qty_memory * 100 / mem_total, 2)
    else:
        load_syslog_memory = 0

    return load_syslog_memory


def put_system_info_in_file(start, file):
    def get_duration(duration):
        hours = int(duration / 3600)
        minutes = int(duration % 3600 / 60)
        seconds = int((duration % 3600) % 60)
        return '{:02d}:{:02d}:{:02d}'.format(hours, minutes, seconds)
    
    lead_time = get_duration((datetime.now() - start).total_seconds())
    print('lead time: {t}'.format(t=lead_time))

    # собрать системную информацию
    info_lst = ['{digit_v}({mode})\n'.format(digit_v=astra_version()[2], mode=astra_version()[1]),
                subprocess.run('uname -r',
                               shell=True,
                               stdout=subprocess.PIPE).stdout.decode("utf-8"),
                subprocess.run("dpkg -l syslog-ng | awk '{print $3}' | tail -n1",
                               shell=True,
                               stdout=subprocess.PIPE).stdout.decode("utf-8"),
                str(lead_time)]

    with open(file, 'a+') as info:
        info.writelines(info_lst)

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

def response():
    return requests.get('https://jira.astralinux.ru').status_code
