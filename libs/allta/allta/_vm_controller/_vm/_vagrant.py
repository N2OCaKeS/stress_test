import os
import requests
import json
from .._libs._system_commands import _System_Commands as system_commands

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
                    if '1.7.6.s' in str(i):
                        box_name = i['1.7.6.s'][0]
                        box_url = i['1.7.6.s'][1]
                elif str(box).startswith('1.8'):
                    if '1.8.1.UU.2.4.s' in str(i):
                        box_name = i['1.8.1.UU.2.4.s'][0]
                        box_url = i['1.8.1.UU.2.4.s'][1]
        return box_name, box_url

    def vagrant_construct(self, provision_script: str):
        """
        Генерирует Vagrantfile с настройками для виртуальных машин.

        Args:
            provision_script (str): Путь до скрипта провиженинга.

        Returns:
            None
        """
        # Проверяем, существует ли директория, и если нет – создаем её.
        os.makedirs(self.path_to_vagrantfile, exist_ok=True)
        vagrantfile_path = os.path.join(self.path_to_vagrantfile, 'Vagrantfile')
        
        with open(vagrantfile_path, 'w') as f:
            # Заголовок Vagrantfile с использованием переменных окружения для бокса
            f.write('Vagrant.configure("2") do |config|\n')
            f.write('  config.vm.box = ENV[\'UPDATE\']\n')
            f.write('  config.vm.box_url = ENV[\'BOX_URL\']\n')
            f.write('  config.vm.synced_folder "/home/iface", "/home/iface"\n\n')
            
            # Формирование массива ВМ
            f.write('  vms = [\n')
            for vm_name, vm_config in self.vms.items():
                cpus = vm_config.get('cpus', "1")
                memory = vm_config.get('memory', "512")
                ip = vm_config.get('ip_bridge', "127.0.0.1")
                # Если disk указан – добавляем ключ :disk, иначе пропускаем
                disk_part = f', :disk => "{vm_config.get("disk")}"' if vm_config.get('disk') else ''
                f.write('    {{ :name => "{}", :hostname => "{}.balance.rbt", :args => "{}", :cpus => "{}", :memory => "{}", :ip => "{}"{} }},\n'.format(
                    vm_name, vm_name, vm_name, cpus, memory, ip, disk_part
                ))
            f.write('  ]\n\n')
            
            # Итерация по массиву ВМ для создания блоков конфигурации
            f.write('  vms.each do |vm|\n')
            f.write('    if ENV[\'VM_NAME\'] == vm[:name] or ENV[\'VM_NAME\'].nil?\n')
            f.write('      config.vm.define vm[:name] do |machine|\n')
            f.write('        machine.vm.hostname = vm[:hostname]\n')
            f.write('        #machine.vm.network "public_network", ip: vm[:ip]\n')
            f.write('        machine.vm.provider "virtualbox" do |vb|\n')
            f.write('          vb.check_guest_additions = false\n')
            f.write('          vb.name = vm[:name]\n')
            f.write('          vb.memory = vm[:memory]\n')
            f.write('          vb.cpus = vm[:cpus]\n')
            f.write('          vb.customize ["modifyvm", :id, "--usb", "off"]\n')
            f.write('          vb.customize ["modifyvm", :id, "--usbehci", "off"]\n\n')
            # Если для ВМ указан параметр disk – добавляем блок создания и подключения диска
            f.write('          if vm.has_key?(:disk)\n')
            f.write('            unless File.exist?("/root/VirtualBox VMs/#{vm[:name]}/#{vm[:name]}_disk.vdi")\n')
            f.write('              ctrl_type = ENV[\'RC\'].nil? || ENV[\'RC\'].start_with?(\'1.8\') ? "sas" : "sas"\n')
            f.write('              ctrl_name = "#{ctrl_type.upcase} Controller #{vm[:name]}"\n\n')
            f.write('              vb.customize [\'createhd\', \'--filename\', "./#{vm[:name]}_disk.vdi", \'--size\', vm[:disk] ]\n')
            f.write('              vb.customize ["storagectl", :id, "--name", ctrl_name, "--add", ctrl_type, \'--portcount\', 1]\n')
            f.write('              vb.customize [\'storageattach\', :id, \'--storagectl\', ctrl_name, \'--port\', 0, \'--device\', 0, \'--type\', \'hdd\', \'--medium\', "./#{vm[:name]}_disk.vdi"]\n')
            f.write('            end\n')
            f.write('          end\n')
            f.write('        end\n\n')
            # Провиженинг: inline-скрипт и вызов внешнего скрипта с передачей аргументов
            f.write('        machine.vm.provision "shell", inline: <<-SHELL\n')
            f.write('            mkdir -p /home/iface && ip link show | grep -e \'2: e\' | tail -n 1 | awk \'{print$2}\' | sed s\'/://\'g > /home/iface/iface\n')
            f.write('          SHELL\n')
            f.write('        machine.vm.provision "shell", path:"{}", args: [vm[:args], ENV[\'KERNEL\'], ENV[\'RC\']]\n'.format(provision_script))
            f.write('      end\n')
            f.write('    end\n')
            f.write('  end\n')
            f.write('end\n')

    def vagrant_up(self, provision_script: str):
        """
        Генерирует Vagrantfile, добавляет образ и запускает виртуальные машины.

        Args:
            provision_script (str): Путь до скрипта провиженинга.

        Returns:
            int: Код завершения выполнения.
        """
        # Генерация Vagrantfile
        self.vagrant_construct(provision_script)
        
        path_to_vagrantfile = self.path_to_vagrantfile 
        rc = self.rc
        box_name, box_url = self._box_wrapper(self)
        kernel = system_commands.check_output_command('uname -r')
        system_commands.cmd('apt install -fy')
        if system_commands.cmd_with_returncode(f'cd {path_to_vagrantfile} && vagrant box add {box_name} {box_url} --force') != 0:
            return 1
        if system_commands.cmd_with_returncode(
            f'cd {path_to_vagrantfile} && UPDATE={box_name} BOX_URL={box_url} KERNEL={kernel} RC={rc} vagrant up --provider=virtualbox'
        ) != 0:
            return 1    
        return 0
