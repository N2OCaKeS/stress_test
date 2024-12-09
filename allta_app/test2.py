import requests
import json



__basic = 'Bearer NzczOTM4MTkwMjE0Oo3C5by4fDOYW0/HMo53S13OIUVu'

def add_testrun_folder(rc):
    main_folder = 2744
    counter = 0

    def __create_testrun_folder(name, parentid=main_folder):
            add_folder_url = f'https://jira.astralinux.ru/rest/tests/1.0/folder/testrun'
            headers = {
                'Authorization': __basic
            }
            data = {
                    "index": -1,
                    "name": name,
                    "projectId": 11200,
                    "parentId": int(parentid)
                    }

            print(data)
            response = requests.post(add_folder_url, headers=headers, json=data)
            print(response.status_code)
            print(response.text)
            value = response.json()
            #config['cycle_tree_index'][name] = str(value['id'])
            #config['cycle_tree_index'] = {k: v for k, v in sorted(config['cycle_tree_index'].items())}
            #write_allta_conf(config)

    while counter < 2:
        counter += 1
        with open('./allta_conf.json', 'r') as r:
            config = json.load(r)

        print(config['cycle_tree_index'].keys())
        if rc not in config['cycle_tree_index'].keys():
            check_len_version = rc.split('.')
            if len(check_len_version) == 4 and check_len_version[3] != 'UU':
                if '.'.join(check_len_version[:3]) in config['cycle_tree_index'].keys():
                    parentid = config['cycle_tree_index']['.'.join(check_len_version[:3])]
                    name = rc
                    __create_testrun_folder(name, parentid)
                else: __create_testrun_folder('.'.join(check_len_version[:3]))
            elif len(check_len_version) == 6 and check_len_version[3] == 'UU':
                if '.'.join(check_len_version[:5]) in config['cycle_tree_index'].keys():
                    parentid = config['cycle_tree_index']['.'.join(check_len_version[:5])]
                    name = rc
                    __create_testrun_folder(name, parentid)
                else: __create_testrun_folder('.'.join(check_len_version[:5]))
            else: 
                name = rc
                __create_testrun_folder(name)



add_testrun_folder('1.8.1.UU.2.3')