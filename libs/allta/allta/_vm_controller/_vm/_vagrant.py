import os
import requests
import json
from ..._system_command.SystemCommands import SystemCommands as system_commands

class _Vagrant():
    """
    Класс для работы с Vagrant.

    Основные функции:
    - Генерация Vagrantfile с настройками для ВМ.
    - Добавление образа (бокса) и запуск ВМ.
    - Определение соответствия версии ОС и доступного бокса.

    Этот класс позволяет автоматизировать процесс создания и настройки ВМ с использованием Vagrant.
    """
    def __init__(self, path_to_vagrantfile: str, box: str, rc: str, vms_date: dict = None):
        """
        Класс для работы с Vagrant

        Args:
            path_to_vagrantfile (str): путь до папки, где будет сгенерирован Vagrantfile
            box (str): версия используемого бокса
            rc (str): версия ОС
            vms_date (dict): словарь с информацией о ВМ, например:
                {
                    "database1": { "ip": "10.177.103.111", "cpus": "4", "memory": "32768", "disk": "40960" },
                    "database2": { "ip": "10.177.103.112", "cpus": "4", "memory": "32768" },
                    ...
                }
            Из имени ВМ будут формироваться поля :name, :hostname и :args.
            Если ключ disk присутствует, то для ВМ будет создан дополнительный диск указанного размера.
        """
        self.path_to_vagrantfile = path_to_vagrantfile
        self.box = box
        self.rc = rc 
        self.vms = vms_date or {}

        

    @staticmethod
    def _box_wrapper(self) -> tuple:
        """
        Определяет соответствие версии операционной системы и доступного бокса.

        Returns:
            tuple: Имя и URL бокса.
        """
        box = self.box
        astra_config_url = 'http://allta.devos.astralinux.ru/rest/api/get-box-config'
        response_ac = requests.get(astra_config_url)
        if response_ac.status_code == 200:
            with open('box-config.json', 'wb') as acb:
                acb.write(response_ac.content)
        else:
            print(f'Failed to get file from {astra_config_url}: {response_ac.status_code}')

        with open('box-config.json', 'r') as r:
            dates = json.loads(r.read())

        true_key = False
        box_name = ''
        box_url = ''
        for i in dates['vagrant_box']:
            if box in str(i):
                for key in i.keys():
                    if str(key).endswith('s'):
                        true_key = key
                        box_name = i[true_key][0]
                        box_url = i[true_key][1]
        if not true_key:
            for i in dates['vagrant_box']:
                if str(box).startswith('1.7'):
                    if '1.7.5.s' in str(i):
                        box_name = i['1.7.5.s'][0]
                        box_url = i['1.7.5.s'][1]
                elif str(box).startswith('1.8'):
                    if '1.8.1s' in str(i):
                        box_name = i['1.8.1s'][0]
                        box_url = i['1.8.1s'][1]
        return box_name, box_url

    def vagrant_up(self):
        """
        Генерирует Vagrantfile, добавляет образ и запускает виртуальные машины.

        Args:
            provision_script (str): Путь до скрипта провиженинга.

        Returns:
            int: Код завершения выполнения.
        """
        # Генерация Vagrantfile
        # self.vagrant_construct(provision_script)
        
        path_to_vagrantfile = self.path_to_vagrantfile 
        rc = self.rc
        box_name, box_url = self._box_wrapper(self)
        kernel = system_commands.check_output_command('uname -r')
        system_commands.cmd('apt install -fy')
        if system_commands.cmd_with_returncode(f'cd {path_to_vagrantfile} && vagrant box add {box_name} {box_url} --force') != 0:
            return 1
        if system_commands.cmd_with_returncode(
            f'cd {path_to_vagrantfile} && UPDATE={box_name} BOX_URL={box_url} KERNEL={kernel} RC={rc} vagrant up --provider=virtualbox --parallel'
        ) != 0:
            return 1    
        return 0
