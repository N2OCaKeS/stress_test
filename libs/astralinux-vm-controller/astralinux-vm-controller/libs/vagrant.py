import libs.system_command as lib_system
import libs.wrapper as wrapper

from os import system

class vagrant():
    """Класс для работы с vagrant
    """
    @staticmethod
    def vagrant(path_to_vagrantfile: str, box: str, rc: str, vms: list): 
        """_summary_

        Args:
            path_to_vagrantfile (str): путь до папки в которой лежит vagrantfile
            box (str): версия используемого бокса
            rc (str): версия ОС
            vms (list): список имен ВМ
        """
        box_name, box_url = wrapper.box_wrapper(box)
        system.cmd('apt install -fy')
        kernel = lib_system.check_output_command('uname -r')
        if system.cmd_with_returncode(f'cd {path_to_vagrantfile} && vagrant box add {box_name} {box_url} --force') != 0:
            return 1
        if system.cmd_with_returncode(f'cd {path_to_vagrantfile} && UPDATE={box_name} BOX_URL={box_url} KERNEL={kernel} RC={rc} vagrant up --provider=virtualbox') != 0:
            return 1    
        return 0
    

    @staticmethod
    def vagrantfile_const():
        pass #TODO Реализовать конструктор который будет генерировать Vagrantfile
