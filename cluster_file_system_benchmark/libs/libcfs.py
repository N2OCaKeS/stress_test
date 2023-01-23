# -*- coding: UTF-8 -*-

# ;===========================================================
# ; Author: rkuznetsov@astralinux.ru
# ; Date: 2022
# ;===========================================================

import subprocess
from time import strftime, time, gmtime

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


def put_system_info_in_file(start, file):
    lead_time = strftime("%H:%M:%S", gmtime(time() - start))
    print('lead time: {t}'.format(t=lead_time))

    # собрать системную информацию
    info_lst = ['{digit_v}({mode})\n'.format(digit_v=astra_version()[0], mode=astra_version()[1]),
                subprocess.run('uname -r',
                               shell=True,
                               stdout=subprocess.PIPE).stdout.decode("utf-8"),
                subprocess.run("dpkg -l auditd | awk '{print $3}' | tail -n1",
                               shell=True,
                               stdout=subprocess.PIPE).stdout.decode("utf-8"),
                str(lead_time)]

    with open(file, 'a+') as info:
        info.writelines(info_lst)
