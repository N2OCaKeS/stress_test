import paramiko
from time import sleep

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