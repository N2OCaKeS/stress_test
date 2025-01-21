import paramiko
import socket
import logging
import requests
from time import sleep
from paramiko import ssh_exception

from src.clonezilla_snap.conf import LOCALBOOT

def remote_cmd(command: str, host: str, user: str, passwd: str, port: int = 22, read=True) -> str:
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy)
        client.connect(hostname=host, username=user, password=passwd, port=port)
        stdin, stdout, stderr = client.exec_command(f'{command}')
        if read:
            data = stdout.read().decode("utf-8") + stderr.read().decode("utf-8")
        else:
            data = None
            sleep(60)
        client.close()
    except paramiko.SSHException as err:
        return err
    if data:
        return data
    else:
        return "No data"

def remote_put_file(host: str, remote_path: str, local_path: str, port: int = 22, user: str = None, passwd: str = None, local_to_remote=True):
    transport = paramiko.Transport((host, port))
    transport.connect(username=user, password=passwd)
    sftp = paramiko.SFTPClient.from_transport(transport)
    if local_to_remote:
        sftp.put(localpath=local_path, remotepath=remote_path)
    else:
        sftp.get(localpath=local_path, remotepath=remote_path)
    sftp.close()
    transport.close()


def ssh_command(command: str, host: str, user: str = "u", passwd: str = "1", port: int = 22):
    try:
        client = paramiko.SSHClient()
        
        client.set_missing_host_key_policy(paramiko.WarningPolicy())
        client.connect(host, port=port, username=user, password=passwd)
        stdin, stdout, stderr = client.exec_command(command)
        response = stdout.read().decode().strip()
        client.close()
    except ssh_exception.SSHException as err:
        response = err
    except TimeoutError as err:
        sleep(60)
        response = err
    return response

def socket_available(reboot_counter=0, max_reboot_attempts=4, stand_ip=None, cs_pass=None, user: str = "u", passwd: str = "1", port: int = 22):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((stand_ip, 22))
    count_for_boot_local = 0
    if result == 0:
        logging.debug('port is open')
    else: 
        logging.error('port is closed')

    while True:
        try:
            if count_for_boot_local == 45 and cs_pass:
                ssh_command(command=LOCALBOOT.format(ip_address=stand_ip),
                            host="10.177.103.10",
                            user=user,
                            passwd=cs_pass,
                            port=port)
            system_status = ssh_command(command='systemctl is-system-running', 
                                        host=stand_ip,
                                        user=user,
                                        passwd=passwd,
                                        port=port)
            if system_status == 'running':
                logging.debug('System is running')
                sock.close()
                return True
            elif system_status == 'degraded':
                not_load_module = ssh_command(command="systemctl --state=failed --no-legend | awk '{print $2}'",
                                              host=stand_ip,
                                              user=user,
                                              passwd=passwd,
                                              port=port)
                logging.error(f'Some modules is not loaded: {system_status}: {not_load_module}')
                if not_load_module == 'astra-mount-lock.service':
                    return True
                else:
                    if reboot_counter >= max_reboot_attempts:
                        logging.error("Maximum reboot attempts reached. Check the system.")
                        return False
                    ssh_command(command='sudo reboot',
                                host=stand_ip,
                                user=user,
                                passwd=passwd,
                                port=port)
                    sleep(60)
                    sock.close()
                    return socket_available(reboot_counter = reboot_counter + 1, 
                                            stand_ip=stand_ip,
                                            user=user,
                                            passwd=passwd,
                                            port=port)
            else:
                logging.error(f'System is not fully loaded yet: {system_status}')
                sleep(30)
                count_for_boot_local+=1
        except paramiko.AuthenticationException:
            sleep(30)
            count_for_boot_local+=1
            continue
        except ssh_exception.NoValidConnectionsError:
            sleep(30)
            count_for_boot_local+=1
            continue
        except ssh_exception.SSHException:
            count_for_boot_local+=1  
            logging.error('Error reading SSH protocol banner')
            sleep(30)
            continue


def func_filter_version(version: str):
    temp = version.split(".")
    if len(temp) > 3 and "UU" not in temp:
        rc = temp[-1]
        cl_version = "".join(temp[:-1]) + "rc" + rc
    elif "UU" in temp and len(temp) > 5:
        rc = temp[-1]
        cl_version = "".join(temp[:-1]) + "rc" + rc
    else:
        cl_version = version
        # print(cl_version)
    return cl_version.replace(".", "")


def convert_stand_name(stand_name):
    if stand_name == "LowServer": 
        num_stand = "stand3"
    elif stand_name == "MiddleServer":
        num_stand = "stand4"
    elif stand_name == "HighServer":
        num_stand = "stand5"
    return num_stand


def busy_status_off(stand):
    res = requests.get(f"http://allta.devos.astralinux.ru/rest/api/busy_status_off/{stand}")