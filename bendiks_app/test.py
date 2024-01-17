import subprocess


def cmd(cmd):
    output = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE).stdout.decode("utf-8")
    return output

login = ''
password = ''
show_config = 'show /system1/bootconfig1/oemhp_uefibootsource'
set_new_config = 'set /system1/bootconfig1/oemhp_uefibootsource2 bootorder='
ssh_command = 'ssh -oKexAlgorithms=+diffie-hellman-group1-sha1 -l % % '
no_fprint = '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
slot_count = 5

try:
    for i in range(0, slot_count + 1, 1):
        answer = cmd(ssh_command + show_config + i, login, password)
        if 'PXE' in answer:
            slot = i
            break
    
    print(cmd(ssh_command + set_new_config + slot, login, password))
except Exception as e:
    print(f'Type:{type(e).__name__}, \nMessage:{str(e)}')


