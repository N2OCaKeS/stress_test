import subprocess
from os import linesep
import paramiko
from src.aggregator.conf import *
from src.aggregator.logger import logger
import socket
from paramiko import ssh_exception



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
            logger.error(f'Function: {function.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')

    return wrapper


@trycorator
def create_remote_file(local_file_path, remote_file_path, ip, user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=ip, username=user, password=password, port=22)
    ftp = client.open_sftp()
    files = ftp.put(local_file_path, remote_file_path)
    ftp.close()
    client.close()


@trycorator
def send_remote_command(command, ip, user, password):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=ip, username=user, password=password, port=22)
    ssh.get_transport().set_keepalive(60)
    chanel = ssh.get_transport().open_session()
    chanel.get_pty()
    chanel.exec_command(command)
    output = chanel.makefile().read().decode('utf-8')
    err_output = chanel.makefile_stderr().read().decode('utf-8')
    if output != '':
        print(f'STDOUT:\n{output}')
    if err_output != '':
        print(f'STDERR:\n{err_output}')
    ssh.close()


@trycorator
def check_remote_command(command, ip, user, password):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=ip, username=user, password=password, port=22)
    ssh.get_transport().set_keepalive(60)
    chanel = ssh.get_transport().open_session()
    chanel.get_pty()
    chanel.exec_command(command)
    output = chanel.makefile().read().decode('utf-8')
    err_output = chanel.makefile_stderr().read().decode('utf-8')
    ssh.close()
    return output, err_output


def remote_ssh_command(command, stand_ip):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.WarningPolicy())
        client.connect(stand_ip, port=22, username=std_user, password='1')
        
        stdin, stdout, stderr = client.exec_command(command, timeout=300)
        
        response = stdout.read().decode().strip()
        error_message = stderr.read().decode().strip()

        if error_message:
            logger.info(f"Error: '{error_message}'")

        logger.info(f"Response: '{response}'")
    finally:
        client.close()
    return response


@trycorator
def get_remote_file(remote_file_path, local_file_path, ip, user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=ip, username=user, password=password, port=22)
    ftp = client.open_sftp()
    files = ftp.get (remote_file_path, local_file_path)
    ftp.close()
    client.close()



def check_running_system(stand_ip):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex((stand_ip, 22))
    if result == 0:
        logger.info('port is open')
    else: 
        logger.error('port is closed')
        sock.close()
        return False

    try:
        system_status = remote_ssh_command('systemctl is-system-running',
                                           stand_ip=stand_ip)
                
        if system_status == 'running':
            logger.info('system is running')
            sock.close()
            return True
        else:
            logger.error(f'System is not fully loaded yet: {system_status}')
            return True
    except paramiko.AuthenticationException:
        logger.error('Authentication failed')
        return False
    except ssh_exception.NoValidConnectionsError:
        logger.error('No valid connections')
        return False
    finally:
        sock.close()


def check_collector(stand_ip):
    def __check_status():
        output = ''
        output = remote_ssh_command(f'systemctl is-active {exporter_name_service}',
                                    stand_ip=stand_ip)
        logger.info(f'Output debug: {output}')

        if output != None and 'active' in output:
            logger.info(f'__check_status(active): {output}')
            return 0
        else: 
            logger.info(f'__check_status: {output}')
            return 1

    if check_running_system(stand_ip):
        if __check_status() == 0:
            logger.info(f'{exporter_name_service} обнаружен, статус active')
            return 0
        else:
            logger.warning(f'{exporter_name_service} не найден, устанавливаю')
            create_remote_file(local_file_path=collector_file,
                            remote_file_path=f'/home/u/{collector_name}',
                            ip=stand_ip,
                            user=std_user,
                            password=std_password)
            logger.debug(remote_ssh_command(command=f'sudo bash /home/u/{collector_name}',
                                            stand_ip=stand_ip))
    
            if __check_status() == 0:
                logger.info(f'{exporter_name_service} обнаружен, статус active')
                return 0
            else:
                logger.error(f'{exporter_name_service} не удалось установить на сервер {stand_ip}\n')
                return 1
    else:
        logger.warning(f'Сервер {stand_ip} недоступен')
        return 1

