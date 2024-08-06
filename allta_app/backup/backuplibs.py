import paramiko
import logging
import time
import os
from tempfile import mkstemp
import subprocess


fd, temp_file_err = mkstemp(dir='/tmp/', suffix='log', text=True)
fd, temp_file_out = mkstemp(dir='/tmp/', suffix='log', text=True)
log_name = f"/tmp/backup_allta.log"
except_num = 1


with open(log_name, 'w') as w:
            w.write('')

backup_logger = logging.getLogger('backup_logger')
backup_logger.setLevel(logging.DEBUG)
handler_bl = logging.FileHandler(log_name)
formatter_bl = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler_bl.setFormatter(formatter_bl)
backup_logger.addHandler(handler_bl)

backup_logger.info('\n\n\nStart backup logging\n')


def command(command, fd_close=False):
    f_err = open(temp_file_err, 'w')
    f_out = open(temp_file_out, 'w')
    result = subprocess.Popen([command], shell=True, stderr=f_err, stdout=f_out)
    output, error = result.communicate()
    text_comm = command
    #rc = result.wait()
    f_err.close()
    f_out.close()

    with open(temp_file_err) as r:
        data_err = r.read()
    with open(temp_file_out) as r:
        data_out = r.read()
        
    if fd_close == True:
        os.close(fd)

    return result.returncode, data_out, data_err, text_comm


def comm_and_log(comm):
    code, output, error, text_comm = command(comm)
    try:
        if error != '':
            backup_logger.error(text_comm)
            backup_logger.error('ErrorCode ' + f'{code}')
            backup_logger.error(error)
            os.unlink(temp_file_err)
        if output != '':
            backup_logger.debug(output)
            os.unlink(temp_file_out)
    except Exception as e:
        global except_num
        backup_logger.error(f'Исключение №{except_num}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')
        except_num = except_num + 1
    return code


def trycorator(function):
    def wrapper(*args, **kwargs):
        try: 
            function(*args, **kwargs)
        except Exception as e:
            backup_logger.error(f'Function: {function.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')

    return wrapper


def check_command(ip, user, password):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(hostname=ip, username=user, password=password, port=22)
    except paramiko.AuthenticationException:
        return 'Authentication failed'

    ssh.close()
    return 'Authentication successful'


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
        backup_logger.debug(f'STDOUT:\n{output}')
    if err_output != '':
        backup_logger.error(f'STDERR:\n{err_output}')
    ssh.close()


@trycorator
def get_remote_file(remote_file_path, local_file_path, ip, user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=ip, username=user, password=password, port=22)
    ftp = client.open_sftp()
    files = ftp.get (remote_file_path, local_file_path)
    ftp.close()
    client.close()




class Backup:
    def __init__(self,
                 ip,
                 user,
                 password):

        self.current_time = time.strftime('%Y%m%d_%H:%M')
        self.sources = ['/home/u/*', '/srv/ftp/modules/*', '/srv/ftp/postgresql/*', '/srv/ftp/python/*', '/srv/ftp/boxes/*', 
                        '/srv/ftp/iso/*', '/etc/systemd/system/acs.service', '/etc/systemd/system/allta.service', 
                        '/etc/systemd/system/bot_allta.service', '/etc/nginx/*']
        self.zip_dir = f"/tmp/backup_allta.zip"
        self.targetdir = f"/home/timonin/hdd/allta_backup/backup.zip"
        self.logdir = f"/home/timonin/hdd/allta_backup/backup.log"
        self.zip_command = f"sudo zip -ur {self.zip_dir} {' '.join(self.sources)}"
        self.ip = ip
        self.user = user
        self.password = password
    
    def run(self):
        comm_and_log(self.zip_command)

        create_remote_file(local_file_path=self.zip_dir, 
                           remote_file_path=self.targetdir, 
                           ip=self.ip, 
                           user=self.user, 
                           password=self.password)
        
        create_remote_file(local_file_path=log_name, 
                           remote_file_path=self.logdir, 
                           ip=self.ip, 
                           user=self.user, 
                           password=self.password)
        

