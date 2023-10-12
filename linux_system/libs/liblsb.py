from shutil import copy
from os import linesep, listdir
from time import strftime, gmtime, time
from subprocess import run, PIPE, DEVNULL, Popen
import ftplib
import requests


def check_output_command(command):
    result = Popen([command], shell=True, stdout=PIPE, stderr=PIPE, universal_newlines=True)
    output, errors = result.communicate()
    output = linesep.join([s for s in output.splitlines() if s])
    errors = linesep.join([s for s in errors.splitlines() if s])
    if errors == "":
        return output
    else:
        return errors


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


def astra_kernel_version():
    return check_output_command('uname -r')


def put_system_info_in_file(start, file):
    lead_time = strftime("%H:%M:%S", gmtime(time() - start))
    print('lead time: {t}'.format(t=lead_time))

    # собрать системную информацию
    info_lst = ['{digit_v}({mode})\n'.format(digit_v=astra_version()[0], mode=astra_version()[1]),
                run('uname -r',
                    shell=True,
                    stdout=PIPE).stdout.decode("utf-8"),
                run("dpkg -l auditd | awk '{print $3}' | tail -n1",
                    shell=True,
                    stdout=PIPE).stdout.decode("utf-8"),
                str(lead_time)]

    with open(file, 'a+') as info:
        info.writelines(info_lst)


def cmd(command):
    run(command, shell=True, stderr=DEVNULL)


def upload_result(dir):

    main_report_html = dir + '/report/lsb_main_report.html'
    report_txt = dir + '/report/lsb_report.txt'
    log = dir + '/log/lsb_log.log'

    # очистить
    file = open(main_report_html, 'w')
    file.close()
    file = open(report_txt, 'w')
    file.close()
    file = open(log, 'w')
    file.close()

    # скопировать результаты
    result_dir = dir + '/byte-unixbench-master/UnixBench/results/'
    for file in listdir(result_dir):
        if file.endswith('.html'):
            copy(result_dir + file, main_report_html)
        elif file.endswith('.log'):
            copy(result_dir + file, log)
        else:
            copy(result_dir + file, report_txt)


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
    try:
        jira = requests.get('https://jira.astralinux.ru').status_code
        life = requests.get('https://life.astralinux.ru').status_code
        return jira, life
    except Exception as e:
        jira, life = str(type(e).__name__, e)
        return jira, life
