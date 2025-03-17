import requests
import json
from libs._system_commands import _system_commands as system_commands

class _Vagrant():


    def __init__(self, path_to_vagrantfile: str, box: str, rc: str, vms: list , vms_date: list = None):
        """Класс для работы с Vagrant

        Args:
            path_to_vagrantfile (str): путь до папки в которой лежит vagrantfile
            box (str): версия используемого бокса
            rc (str): версия ОС
            vms (list): список имен ВМ
        """
        self.path_to_vagrantfile = path_to_vagrantfile
        self.box = box
        self.rc = rc 
        self.vms = vms_date

    @staticmethod
    def _box_wrapper(self) -> tuple:
        """
        Метод определяет соответствие версий ОС и доступности
        бокса, возвращает соответствующий url и name
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

        if true_key == False:
            for i in dates['vagrant_box']:
                if str(box).startswith('1.7'):
                    if '1.7.6.s' in str(i):
                        box_name = i['1.7.6.s'][0]
                        box_url = i['1.7.6.s'][1]
                elif str(box).startswith('1.8'):
                    if '1.8.1.UU.2.4.s' in str(i):
                        box_name = i['1.8.1.UU.2.4.s'][0]
                        box_url = i['1.8.1.UU.2.4.s'][1]

        return box_name, box_url
    
    def vagrant_up(self):
        """Добавление образа в vagrant, и поднятие ВМ с помощью Vagrant

        Args:
            path_to_vagrantfile (str): путь до папки в которой лежит vagrantfile
            box (str): версия используемого бокса
            rc (str): версия ОС
            vms (list): список имен ВМ
        """
        path_to_vagrantfile = self.path_to_vagrantfile 
        rc = self.rc
        box_name, box_url = self._box_wrapper(self)
        system_commands.cmd('apt install -fy')
        kernel = system_commands.check_output_command('uname -r')
        if system_commands.cmd_with_returncode(f'cd {path_to_vagrantfile} && vagrant box add {box_name} {box_url} --force') != 0:
            return 1
        if system_commands.cmd_with_returncode(f'cd {path_to_vagrantfile} && UPDATE={box_name} BOX_URL={box_url} KERNEL={kernel} RC={rc} vagrant up --provider=virtualbox') != 0:
            return 1    
        return 0
    
    def vagrant_construct(self): # TODO Реализовать конструктор для генерации файлов
        pass