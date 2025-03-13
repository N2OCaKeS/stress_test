import requests
import json

@staticmethod
def box_wrapper(box: str) -> tuple:
    """
    Метод определяет соответствие версий ОС и доступности
    бокса, возвращает соответствующий url и name

    Args:
        box (str): версия бокса
    """
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