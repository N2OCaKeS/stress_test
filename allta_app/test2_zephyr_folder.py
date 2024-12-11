import requests
import json

def write_allta_conf(data):
    with open('./allta_conf.json', 'w') as w:
        json.dump(data, w, indent=4)




def add_testrun_folder(rc):
    main_folder = 2744
    counter = 0

    def __get_folder_tree_id(rc, headers):
        search_url = 'https://jira.astralinux.ru/rest/tests/1.0/testrun/search?'
        filer_to_url = 'fields=id,key,folder,name&query=testRun.projectId+IN+(11200)+AND+testRun.folderTreeId+IN+({})&maxResults={}&archived=false'
        
        response = requests.get(search_url + filer_to_url.format(main_folder, 2), headers=headers)
        print(f'Check stress_test total folders count status: {response.status_code}')
        #print(response.text) #debug
        folder_counts = response.json()['total']
        print(f'Total folder counts: {folder_counts}')
        response = requests.get(search_url + filer_to_url.format(main_folder, folder_counts), headers=headers)
        print(f'Get all folders date status: {response.status_code}')
        #print(response.text) #debug
        report_folders = response.json()

        zephyr_folders_dict = {
            report_folders['results'][folder]['name'].split('_')[0]: report_folders['results'][folder]['folder']['id']
            for folder in range(len(report_folders['results']))
        }
        print(zephyr_folders_dict) #debug

        try:
            created_folder_tree_id = zephyr_folders_dict[rc]
        except Exception as e:
            print(f'{type(e).__name__}: {str(e)}')
            return ''

        return created_folder_tree_id

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
            #print(response.status_code)
            #print(response.text)
            value = response.json()
            config['cycle_tree_index'][name] = __get_folder_tree_id(name, headers)
            config['cycle_tree_index'] = {k: v for k, v in sorted(config['cycle_tree_index'].items())}
            #print(config['cycle_tree_index'])
            write_allta_conf(config)

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



#add_testrun_folder('1.8.1.UU.2.4')



#print(get_folder_tree_id('1.8.1.UU.2.3'))






report_folder = {
    "total":364,
    "maxResults":2,
    "results":[{
        "folder":{
            "parent":{
                "customFieldValues":[],
                "name":"stress_test",
                "index":14,
                "id":2744,
                "folderType":"TEST_RUN",
                "projectId":11200},
            "customFieldValues":[],
            "index":7,
            "id":2967,
            "folderType":"TEST_RUN",
            "projectId":11200},
        "name":"debian10_orel_4.19.0-24_stand1",
        "id":2989,
        "key":"BT-C2913"},
        {"folder":{
            "parent":{
                "customFieldValues":[],
                "name":"stress_test",
                "index":14,
                "id":2744,
                "folderType":"TEST_RUN",
                "projectId":11200},
            "customFieldValues":[],
            "index":8,
            "id":2974,
            "folderType":"TEST_RUN",
            "projectId":11200},
        "name":"debian10-5.15_orel_5.15.10_stand1",
        "id":3013,
        "key":"BT-C2935"}],
    "startAt":0}




#print(report_folder["results"][1]["folder"]["id"])

#zephyr_folders_dict = {
#    report_folder['results'][folder]['name']: report_folder['results'][folder]['folder']['id']
#    for folder in range(len(report_folder['results']))
#}


#print(filtered_data)


main_folder = 2744
headers = {
                'Authorization': __basic
            }


def __get_folder_tree_id(rc, headers):
        search_url = 'https://jira.astralinux.ru/rest/tests/1.0/testrun/search?'
        filer_to_url = 'fields=id,folder&query=testRun.projectId+IN+(11200)+AND+testRun.folderTreeId+IN+({})&maxResults={}&archived=false'
        
        response = requests.get(search_url + filer_to_url.format(main_folder, 2), headers=headers)
        print(f'Check stress_test total folders count status: {response.status_code}')
        print(response.text) #debug
        #folder_counts = response.json()['total']
        #print(f'Total folder counts: {folder_counts}')
        #response = requests.get(search_url + filer_to_url.format(main_folder, folder_counts), headers=headers)
        #print(f'Get all folders date status: {response.status_code}')
        #print(response.text) #debug
        #report_folders = response.json()

        #zephyr_folders_dict = {
        #    report_folders['results'][folder]['name'].split('_')[0]: report_folders['results'][folder]['folder']['id']
        #    for folder in range(len(report_folders['results']))
        #}
        #print(zephyr_folders_dict) #debug

        #try:
        #    created_folder_tree_id = zephyr_folders_dict[rc]
        #except Exception as e:
        #    print(f'{type(e).__name__}: {str(e)}')
        #    return ''

        #return created_folder_tree_id


#__get_folder_tree_id('1.8.1.UU.2.3', headers)




add_folder_url = f'https://jira.astralinux.ru/rest/tests/1.0/folders/testrun'
headers = {
    'Authorization': __basic
}

response = requests.get(add_folder_url, headers=headers)
print(response.status_code)
print(response.text)