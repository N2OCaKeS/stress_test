import subprocess
from os import linesep
import paramiko
import pandas as pd



def trycorator(function):
    def wrapper(*args, **kwargs):
        try: 
            function(*args, **kwargs)
        except Exception as e:
            print(f'Function: {function.__name__}\nError is: {str(type(e).__name__)}\nMessage: {str(e)}')

    return wrapper



class system:
    """
    Класс для обращения к системе
    """
    @staticmethod
    def check_output_command(command: str) -> str:
        result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        output, errors = result.communicate()
        output = linesep.join([s for s in output.splitlines() if s])
        errors = linesep.join([s for s in errors.splitlines() if s])
        return output if not errors else errors

    @staticmethod
    def cmd_with_returncode(command: str) -> int:
        return subprocess.run(command, shell=True).returncode

    @staticmethod
    def cmd(command: str):
        return subprocess.run(command, shell=True)
    


@trycorator
def create_remote_file(local_file_path, remote_file_path, ip, user, password, port):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=ip, username=user, password=password, port=port)
    ftp = client.open_sftp()
    files = ftp.put(local_file_path, remote_file_path)
    ftp.close()
    client.close()


@trycorator
def send_remote_command(command, ip, user, password, port):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(hostname=ip, username=user, password=password, port=port)
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
def get_remote_file(remote_file_path, local_file_path, ip, user, password, port):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(hostname=ip, username=user, password=password, port=port)
    ftp = client.open_sftp()
    files = ftp.get (remote_file_path, local_file_path)
    ftp.close()
    client.close()



def results_handler(file, name):
    with open(file, 'r') as r:
        results = r.readlines()

    print(f'Необработанные результаты: \n{results}')

    data = {
        'Время проверки подписи, сек': {
            'Подписано': results[0].strip(),
            'Не подписано': results[2].strip()},
        'Количество записей \"DIGSIG:[ERROR]  VERIFICATION FAILED\"': {
            'Подписано': results[1].strip(),
            'Не подписано': results[3].strip()}
    }

    df = pd.DataFrame(data).T
    df = df[['Подписано', 'Не подписано']]
    print(df)
    df.to_html(name)

