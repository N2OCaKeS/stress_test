import argparse
import datetime
from libs.libs import send_remote_command, get_remote_file, create_remote_file
from manage_vm import ManageVM
from conf import vCPU, RAM

"""
    1) Поднимаем 1 ВМ с 2 ядра 2 гига
    2) Перекидываем туда checker_logs и запускаем
    3) с ВМ на хост перекидываем файл status.txt и смотрим статус
    4) Выключаем и удаляем ВМ
"""

parser = argparse.ArgumentParser()

parser.add_argument('-vbox', 
                    action='store',
                    required=True,
                    help='vbox name',
                    dest='VBOX')

parser.add_argument('-kernel', 
                    action='store',
                    required=True,
                    help='kernel version',
                    dest='KERNEL')

args = parser.parse_args()

# 1 ---
vm  = ManageVM(rc_vbox=args.VBOX, #args.VBOX,
               #testdir=...,
               vm_count=1,
               kernel=args.KERNEL,
               vcpu=vCPU,
               ram=RAM)

vm.prepare_and_start_vm()
data_vm = vm.vm_dates
print(data_vm)

# 2 ***
start_time = datetime.datetime.now()
print(start_time)

# status = "TEST STARTED"
# try:
#     create_remote_file(local_file_path="conf.py", 
#                     remote_file_path=f"/home/{data_vm['login']}/conf.py",
#                     ip=data_vm['ip'],
#                     user=data_vm['login'],
#                     password=data_vm['password'])

#     # create_remote_file(local_file_path="generator_logs.py", 
#     #                    remote_file_path="/home/vagrant/generator_logs.py",
#     #                    ip=data_vm['ip'],
#     #                    user=data_vm['login'],
#     #                    password=data_vm['password'])

#     create_remote_file(local_file_path="new_checker_logs.py", 
#                        remote_file_path=f"/home/{data_vm['login']}/new_checker_logs.py",
#                        ip=data_vm['ip'],
#                        user=data_vm['login'],
#                        password=data_vm['password'])

#     send_remote_command(command="sudo python3 new_checker_logs.py",
#                         ip=data_vm['ip'],
#                         user=data_vm['login'],
#                         password=data_vm['password'])
#     # 3 |||
#     get_remote_file(remote_file_path=f"/home/{data_vm['login']}/status.txt",
#                     local_file_path="status.txt",
#                     ip=data_vm['ip'],
#                     user=data_vm['login'],
#                     password=data_vm['password'])
# except Exception as err:
#     status = "TEST ERROR"
#     print(err)
    
# if status != "TEST ERROR":
#     with open("status.txt", 'r') as status_file:
#         status = status_file.readline()
#         print(f"STATUS: {status}")

end_time = datetime.datetime.now()
print(end_time)
# 4 +++
vm.destroy_vm()