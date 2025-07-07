import re
import shutil
import os.path
import threading
import subprocess
# import numpy as np
import pandas as pd
import libs.libtable as libtable
import libs.libscanner as libscanner

from time import sleep, ctime
from datetime import datetime
from os import chmod, mkdir, getcwd, path

from libs.libs import cmd
from find_err_in_logs import collecting_logs
from sng_conf import SERVICE_COUNT, TIME_EXEC, TIME_EXEC_ST3_ST4, REPORT_PATH, IMAGE_WIDTH, IMAGE_HEIGHT, INFO_FILENAME, REPORT_FILENAME, VENV_PATH
from libs.libsng import (astra_version, 
                         check_service_status, 
                         get_memory_load_by_syslog, 
                         put_system_info_in_file, 
                         upload_results_to_ftp,
                         response)

from libs.libs import send_remote_command, get_remote_file, create_remote_file
from manage_vm import ManageVM
from conf import vCPU, RAM, VM_INFONAME, VM_KERNEL, STATUS_FILENAME, VM_PACKAGE_VERS, TABLE_STATUSES

class SNGBenchMarkTest():
    def __init__(self, log_level, stand, tcv, tcyc) -> None:
        self.log_level = log_level
        self.stand = stand
        self.tcv = tcv
        self.tcyc = tcyc
        self.lst_service_names = []
        self.time_start_script = datetime.now()
        # Если отстуствует директория для отчета, необходимо создать
        if os.path.exists(REPORT_PATH) is False:
            mkdir(REPORT_PATH)
    
    def prepare(self):
        '''
            Установка необходимого уровня логирования
        '''
        default_filter = r'(filter\sf_(dbg|debug|info|notice|warn|err(or)?|crit)\s\{\slevel\().+(\).*)'
        new_filter = r'\1{ll}\4'.format(ll=self.log_level)

        with open('/etc/syslog-ng/syslog-ng.conf', 'r') as main_syslog_ng_conf:
            new_config = re.sub(default_filter, new_filter, main_syslog_ng_conf.read())
        with open('/etc/syslog-ng/syslog-ng.conf', 'w') as main_syslog_ng_conf:
            main_syslog_ng_conf.write(new_config)
        
        '''
            Создание юнит файлов с логерами
        '''
        dir = getcwd()

        for service_num in range(1, SERVICE_COUNT+1):

            service_name = 'dirtylogger{}.service'.format(service_num)
            shutil.copy('sng_service_template.py', '/tmp/dirtylogger{}.py'.format(service_num))
            chmod('/tmp/dirtylogger{}.py'.format(service_num), 0o0777)

            unit = ['[Unit]\n',
                    'Description=test service by ivelikanov@astralinux.ru\n',
                    'After=multi-user.target\n',
                    '[Service]\n',
                    'Type=simple\n',
                    'Restart=always\n',
                    'WorkingDirectory={}/\n'.format(dir),
                    'OOMScoreAdjust = -100\n', # Prohibition on the use of the out-of-memory service and the OOM trigger mechanism
                    f'ExecStart={VENV_PATH} /tmp/dirtylogger{service_num}.py\n',
                    'TimeoutSec=1\n',
                    '[Install]\n',
                    'WantedBy = multi - user.target\n']
            

            with open('/etc/systemd/system/{}'.format(service_name), 'w') as test_unit:
                test_unit.writelines(unit)
            
            self.lst_service_names.append(service_name)
    
    def run_test(self):
        for service_name in self.lst_service_names:
            sleep(0.01)
            cmd("systemctl start {}".format(service_name))
        
        data_cpu, data_memory, data_syslog_memory, data_disk, data_time = [], [], [], [], []
        sc = libscanner.Scanner()

        '''
            Добавление нулевых значений для корректного подсчета рейтинга
        '''
        data_cpu.append(0)
        data_memory.append(0)
        data_syslog_memory.append(0)    
        data_disk.append(sc.get_disk_load())
        data_time.append(0)

        if self.stand == '3' or self.stand == '4':
            time_exec = TIME_EXEC_ST3_ST4 * 60
        else: time_exec = TIME_EXEC * 60
        qty_sec_after_start = 1

        '''
            Сбор данных с CPU, Memory, Disk 
        '''
        while time_exec > 0:
            if not check_service_status('syslog-ng'):
                print("Service Syslog-NG is not running!")
                file_status_sng = open("status_syslog-ng.txt", "w+")
                subprocess.run("systemctl status syslog-ng", shell=True, stdout=file_status_sng)
                file_status_sng.close()
                break
                
            sleep(0.25)
            data_cpu.append(sc.get_cpu_load())
            data_memory.append(sc.get_memory_load())
            data_syslog_memory.append(get_memory_load_by_syslog())
            data_disk.append(sc.get_disk_load())
            data_time.append(qty_sec_after_start)
            time_exec -= 1
            qty_sec_after_start += 1


        '''
            Остановка сервисов
        '''
        for service_name in self.lst_service_names:
            cmd("systemctl stop {}".format(service_name))
        

        '''
            Создание отчета
        '''
        sng_data = pd.DataFrame(data={'load_cpu': data_cpu, 'load_memory': data_memory, 'load_syslog_ng_memory': data_syslog_memory, 'load_disk': data_disk}, index=data_time)
        # scaler = preprocessing.MinMaxScaler()
        # Нормализуем данные
        # d = scaler.fit_transform(sng_data)
        # Строим новый dataframe с нормированными данными
        # scaled_sng_data = pd.DataFrame(d, columns=sng_data.columns)
        
        report = libtable.Report(os.path.expanduser(REPORT_PATH), float(IMAGE_WIDTH), float(IMAGE_HEIGHT))
        rating_cpu = report.get_rating(x=data_time, y=sng_data['load_cpu'])
        rating_memory = report.get_rating(x=data_time, y=sng_data['load_memory'])
        rating_syslog_memory = report.get_rating(x=data_time, y=sng_data['load_syslog_ng_memory'])
        rating_disk = report.get_rating(x=data_time, y=sng_data['load_disk'])
        total_rating = report.get_total_rating(self.stand, [rating_cpu, rating_memory, rating_syslog_memory, rating_disk])

        # Вывод данных на экран
        print("Total rating:", total_rating)

        '''
            Создание текстового файла с отчетом
        '''
        with open('{}/sng_report.txt'.format(os.path.expanduser(REPORT_PATH)), 'w') as report_txt:
            report_txt.writelines('Astra_version: {}\n'.format(astra_version()[2]))
            report_txt.writelines('Astra_mode: {}\n'.format(astra_version()[1]))
            report_txt.writelines('Kernel: {}\n'.format(astra_version()[3]))
            report_txt.writelines('Service_count: {}\n'.format(SERVICE_COUNT))
            report_txt.writelines('Load_time_execution: {} minutes\n'.format(TIME_EXEC))
            report_txt.writelines('Rating_CPU: {}\n'.format(rating_cpu))
            report_txt.writelines('Rating_memory: {}\n'.format(rating_memory))
            report_txt.writelines('Rating_Syslog-NG_memory: {}\n'.format(rating_syslog_memory))
            report_txt.writelines('Rating_disk: {}\n'.format(rating_disk))
            report_txt.writelines('Total_rating: {}\n'.format(total_rating))
        
        '''
            Построение графиков
        '''
        graph_load_cpu = report.create_graph(x=data_time[1:], 
                                             y=data_cpu[1:],
                                             filename='sng_cpu', 
                                             title_graph='Load CPU', 
                                             y_label="CPU %", 
                                             x_rlim=TIME_EXEC * 60 - 1)
        
        graph_load_memory = report.create_graph(x=data_time[1:], 
                                                y=data_memory[1:],
                                                filename='sng_memory', 
                                                title_graph='Load memory', 
                                                y_label="Memory %", 
                                                x_rlim=TIME_EXEC * 60 - 1)

        graph_load_syslog_memory = report.create_graph(x=data_time[1:], 
                                                       y=data_syslog_memory[1:],
                                                       filename='sng_syslog_memory', 
                                                       title_graph='Load syslog-ng memory', 
                                                       y_label="Memory %", 
                                                       x_rlim=TIME_EXEC * 60 - 1)

        graph_load_disk = report.create_graph(x=data_time[1:], 
                                              y=data_disk[1:],
                                              filename='sng_disk', 
                                              title_graph='Load disk', 
                                              y_label="Disk %", 
                                              x_rlim=TIME_EXEC * 60 - 1)

        report.data_to_dataframe_csv({'time': data_time, 
                                      'load_cpu': data_cpu, 
                                      'load_memory': data_memory, 
                                      'load_syslog_ng_memory': data_syslog_memory, 
                                      'load_disk': data_disk},
                                      filename='sng_data')
        
        '''
            Построение HTML отчета
        '''
        report.create_html([graph_load_cpu, graph_load_memory, graph_load_syslog_memory, graph_load_disk], total_rating, SERVICE_COUNT, TIME_EXEC)
        
        '''
        Сбор и фильтрация логов по времени (после запуска тестового сценария)
        '''
        print("\nСбор логов...\n")
        collecting_logs(os.path.expanduser(REPORT_PATH), self.time_start_script)


        '''
            Запись информации о тестовом стенде
        '''
        info_file = open(INFO_FILENAME, 'w')
        info_file.close()

        put_system_info_in_file(self.time_start_script, INFO_FILENAME)

        '''
            Архивация результатов
        '''
        print("Создание архива с отчетом...")
        libtable.Report.create_tar(os.path.expanduser(REPORT_PATH))
        print("Готово.")

        #upload_results_to_ftp(self.tcv, f'{REPORT_PATH}/{REPORT_FILENAME}', f'syslog-ng_{self.tcyc}_{REPORT_FILENAME}')
        return True


class SNGCheckWriteLogsTest():
    STATUS_STARTED = "STATUS STARTED"
    STATUS_ERROR = "TEST ERROR"
    STATUS_PASSED = "TEST PASSED"

    def __init__(self, vmcount, vbox, kernel) -> None:
        self.vmcount = vmcount
        self.vbox = vbox
        self.kernel = kernel
        self.status = ""
        self.time_start_script = datetime.now()
        # Если отстуствует директория для отчета, необходимо создать
        if os.path.exists(REPORT_PATH) is False:
            mkdir(REPORT_PATH)

    def task_test(self, thr_index):
        create_remote_file(local_file_path="conf.py", 
                           remote_file_path=f"/home/{self.data_vm[f"testvm{thr_index}"]['login']}/conf.py",
                           ip=self.data_vm[f"testvm{thr_index}"]['ip'],
                           user=self.data_vm[f"testvm{thr_index}"]['login'],
                           password=self.data_vm[f"testvm{thr_index}"]['password'])

        # create_remote_file(local_file_path="generator_logs.py", 
        #                    remote_file_path="/home/vagrant/generator_logs.py",
        #                    ip=data_vm['ip'],
        #                    user=data_vm['login'],
        #                    password=data_vm['password'])

        create_remote_file(local_file_path="new_checker_logs.py", 
                           remote_file_path=f"/home/{self.data_vm[f"testvm{thr_index}"]['login']}/new_checker_logs.py",
                           ip=self.data_vm[f"testvm{thr_index}"]['ip'],
                           user=self.data_vm[f"testvm{thr_index}"]['login'],
                           password=self.data_vm[f"testvm{thr_index}"]['password'])

        send_remote_command(command="sudo python3 new_checker_logs.py",
                            ip=self.data_vm[f"testvm{thr_index}"]['ip'],
                            user=self.data_vm[f"testvm{thr_index}"]['login'],
                            password=self.data_vm[f"testvm{thr_index}"]['password'])
        # 3 |||
        get_remote_file(remote_file_path=f"/home/{self.data_vm[f"testvm{thr_index}"]['login']}/status.txt",
                        local_file_path=f"{REPORT_PATH}/status{thr_index}.txt",
                        ip=self.data_vm[f"testvm{thr_index}"]['ip'],
                        user=self.data_vm[f"testvm{thr_index}"]['login'],
                        password=self.data_vm[f"testvm{thr_index}"]['password'])

    def final_result(self, statuses):
        status_error = []
        status_fail = []
        for vmname, status in statuses.items():
            if status != self.STATUS_PASSED:
                if status == self.STATUS_ERROR:
                    status_error.append((vmname, status))
                elif "FAILED" in status:
                    status_fail.append((vmname, status))
        if len(status_error) > 0:
            return f'{self.STATUS_ERROR} on {len(status_error)} VM'
        elif len(status_fail) > 0:
            return f'TEST FAILED on {len(status_fail)} VM'
        elif len(status_error) > 0 and len(status_fail) > 0:
            return f'{self.STATUS_ERROR} on {len(status_error)} VM and TEST FAILED on {len(status_fail)} VM'
        else:
            return self.STATUS_PASSED
    
    def get_info_from_vms(self):
        send_remote_command(command=f'cat /etc/astra/build_version > /home/{self.data_vm[f"testvm1"]['login']}/av.txt',
                            ip=self.data_vm[f'testvm1']['ip'], 
                            user=self.data_vm[f"testvm1"]['login'], 
                            password=self.data_vm[f"testvm1"]['password'])
        
        send_remote_command(command=f'uname -r > /home/{self.data_vm[f"testvm1"]['login']}/kernel.txt',
                            ip=self.data_vm[f'testvm1']['ip'], 
                            user=self.data_vm[f"testvm1"]['login'], 
                            password=self.data_vm[f"testvm1"]['password'])
        
        send_remote_command(command="dpkg -l syslog-ng | awk '{{print $3}}' | tail -n1 > /home/{user}/package_version.txt".format(user=self.data_vm[f"testvm1"]['login']),
                            ip=self.data_vm[f'testvm1']['ip'], 
                            user=self.data_vm[f"testvm1"]['login'], 
                            password=self.data_vm[f"testvm1"]['password'])

        get_remote_file(remote_file_path=f'/home/{self.data_vm[f"testvm1"]['login']}/av.txt',
                        local_file_path=f'{REPORT_PATH}/{VM_INFONAME}',
                        ip=self.data_vm[f'testvm1']['ip'], 
                        user=self.data_vm[f"testvm1"]['login'], 
                        password=self.data_vm[f"testvm1"]['password']) 
        
        get_remote_file(remote_file_path=f'/home/{self.data_vm[f"testvm1"]['login']}/kernel.txt',
                        local_file_path=f'{REPORT_PATH}/{VM_KERNEL}',
                        ip=self.data_vm[f'testvm1']['ip'], 
                        user=self.data_vm[f"testvm1"]['login'], 
                        password=self.data_vm[f"testvm1"]['password'])

        get_remote_file(remote_file_path=f'/home/{self.data_vm[f"testvm1"]['login']}/package_version.txt',
                        local_file_path=f'{REPORT_PATH}/{VM_PACKAGE_VERS}',
                        ip=self.data_vm[f'testvm1']['ip'], 
                        user=self.data_vm[f"testvm1"]['login'], 
                        password=self.data_vm[f"testvm1"]['password'])  

    def prepare(self):
        self.vm  = ManageVM(rc_vbox=self.vbox,
                            vm_count=self.vmcount,
                            kernel=self.kernel,
                            vcpu=vCPU,
                            ram=RAM)

        self.vm.prepare_and_start_vm()
        self.data_vm = self.vm.vm_dates
        print(self.data_vm)
        self.get_info_from_vms()

    def run_test(self):
        start_time = datetime.now()
        print(start_time)

        self.status = self.STATUS_STARTED
        try:
            threads = [threading.Thread(target=self.task_test, args=(idx,)) for idx in range(1, self.vmcount + 1)]
            for thread in threads:
                thread.start()

            for thread in threads:
                thread.join()

        except Exception as err:
            self.status = self.STATUS_ERROR
            print(err)
            
        if self.status != self.STATUS_ERROR:
            statuses_dct = {}
            for index in range(1, len(threads) + 1):
                with open(f"{REPORT_PATH}/status{index}.txt", 'r') as status_file:
                    status = status_file.readline()
                    statuses_dct[f'testvm{index}'] = status
                    status_table = pd.DataFrame(list(statuses_dct.items()), columns=["VM name", "Test status"])
                    status_table.to_html(f"{REPORT_PATH}/{TABLE_STATUSES}", escape=False, index=False)
            self.status = self.final_result(statuses=statuses_dct)
            
        else:
            self.status = self.STATUS_ERROR
        
        with open(f"{REPORT_PATH}/{STATUS_FILENAME}", "w") as itog_status_file:
            itog_status_file.write(self.status)
        
        # 4 +++
        # self.vm.destroy_vm()

        end_time = datetime.now()
        print(end_time)
        '''
            Запись информации о тестовом стенде
        '''
        info_file = open(INFO_FILENAME, 'w')
        info_file.close()
        put_system_info_in_file(self.time_start_script, INFO_FILENAME)
        if self.status == self.STATUS_ERROR:
            return False
        else:
            return True