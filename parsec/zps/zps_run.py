import requests
import json
import subprocess
import os



class system:
    """
    Класс для обращения к системе
    """
    @staticmethod
    def check_output_command(command: str) -> str:
        result = subprocess.Popen([command], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        output, errors = result.communicate()
        output = os.linesep.join([s for s in output.splitlines() if s])
        errors = os.linesep.join([s for s in errors.splitlines() if s])
        return output if not errors else errors

    @staticmethod
    def cmd_with_returncode(command: str) -> int:
        return subprocess.run(command, shell=True).returncode

    @staticmethod
    def cmd(command: str):
        return subprocess.run(command, shell=True)
    



def box_wrapper(box: str, dates: dict) -> tuple:
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
                if '1.7.1.s' in str(i):
                    box_name = i['1.7.1.s'][0]
                    box_url = i['1.7.1.s'][1]
            elif str(box).startswith('1.8'):
                if '1.8.0.s' in str(i):
                    box_name = i['1.8.0.s'][0]
                    box_url = i['1.8.0.s'][1]
    
    return box_name, box_url

astra_config_url = 'http://allta.devos.astralinux.ru/rest/api/get-box-config'
response_ac = requests.get(astra_config_url)
if response_ac.status_code == 200:
    with open('box-config.json', 'wb') as acb:
        acb.write(response_ac.content)
else:
    print(f'Failed to get file from {astra_config_url}: {response_ac.status_code}')

with open('box-config.json', 'r') as r:
    dates = json.loads(r.read())

box = '1.8.1.16'
kernel = '6.1.90-1-generic'
box_name, box_url = box_wrapper(box, dates)
command = system()


###
#Run Test
###

command.cmd(f'vagrant box add {box_name} {box_url} --force')
command.cmd(f'UPDATE={box_name} BOX_URL={box_url} KL={kernel} RC={box} vagrant up --provider=virtualbox')


