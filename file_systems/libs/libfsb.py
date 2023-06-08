import subprocess
import ftplib


def astra_version():
    version = []
    # astra_digit_version = subprocess.run("cat /etc/os-release | grep '^VERSION_ID'",
    #                                      shell=True,
    #                                      stdout=subprocess.PIPE,
    #                                      stderr=subprocess.DEVNULL).stdout.decode("utf-8")
    # if "2.12" in astra_digit_version:
    #     version.append("2.12")
    # elif "1.6" in astra_digit_version:
    #     version.append("1.6")
    # elif "1.7" in astra_digit_version:
    #     version.append("1.7")
    # elif "4.7" in astra_digit_version:
    #     version.append("4.7")
    # elif "8.1" in astra_digit_version:
    #     version.append("8.1")
    # else:
    #     print("Version of distribution not found")
    #     exit(2)

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