import subprocess
from os import linesep
import paramiko


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


def create_remote_file(local_file_path, remote_file_path, ip, user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=ip, username=user, password=password, port=22)
    ftp = client.open_sftp()
    files = ftp.put(local_file_path, remote_file_path)
    ftp.close()
    client.close()


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


def get_remote_file(remote_file_path, local_file_path, ip, user, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=ip, username=user, password=password, port=22)
    ftp = client.open_sftp()
    files = ftp.get (remote_file_path, local_file_path)
    ftp.close()
    client.close()

