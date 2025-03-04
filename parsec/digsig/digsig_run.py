import requests
import json

from digsiglib import system, get_remote_file

  


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

box = '1.8.1.UU.2.4'
kernel = '6.1.90-1-generic'
box_name, box_url = box_wrapper(box, dates)
command = system()


###
#Run Test
###

command.cmd('sudo bash vbox_prepare.sh')
command.cmd(f'vagrant box add {box_name} {box_url} --force')
command.cmd(f'UPDATE={box_name} BOX_URL={box_url} KL={kernel} RC={box} vagrant up --provider=virtualbox')

get_remote_file(remote_file_path='/vagrant/results.txt',
                local_file_path='results.txt',
                ip='192.168.56.11', 
                user='u', 
                password='1') 
                            


