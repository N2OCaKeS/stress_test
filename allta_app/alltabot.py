from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import types 
from aiogram.filters.command import Command, CommandObject
from aiogram.utils.formatting import Text
import asyncio
import aiofiles
from random import randrange
from os.path import isfile
from os import remove
import json
import requests
from bs4 import BeautifulSoup
import re
from libs.liballta import (ReleaseToRepo, 
                           get_kernels_from_rc, 
                           run_command_on_stand,
                           busy_status_control)
from libs.zefir import ZefirResultTable, ZefirTestRun
from allta_image_conf import JIRA_URL, LowServer_group, MiddleServer_group, stands_type, stands_ip
from time import sleep



with open('/home/u/key.conf', 'r') as r:
    API_TOKEN = r.read().strip()
bot = Bot(API_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

with open('/home/u/tokens.json', 'r') as r:
    tokens = json.load(r)
__conf_token = tokens['conf_token']
__username = tokens['username']
__basic = tokens['jira_token']
__password = tokens['pass']

path_chlog = '/home/u/git/stress_test/allta_app/ChangeLog'
path_tgbot_conf = '/home/u/telegrambotconf.json'
path_stand3 = '/home/u/git/stress_test/allta_app/telegrambot/results_stand3.txt'
path_stand4 = '/home/u/git/stress_test/allta_app/telegrambot/results_stand4.txt'
chat_id = '-1002121821530'
SERVER_ACS_IP_OR_NAME = "10.177.103.10"
SERVER_ACS_PORT = 9999
BASE_URL = f"http://{SERVER_ACS_IP_OR_NAME}:{SERVER_ACS_PORT}"
fotos = [
'http://allta.devos.astralinux.ru/rest/api/get-ping1-pic',
'http://allta.devos.astralinux.ru/rest/api/get-ping2-pic'
]

help_text = """Доступные команды:
/log - получить прогресс выполнения прогона
/status - узнать статус прогона
/id - узнать ID чата
/vpn - доступные для использования в телефоне настройки VPN'
/addrc - добавить конфигурацию новой версии релиз кандидата
/acs - сделать снимок для выбранного стенда
/update_stp - обновить состав тестового прогона
/add_testrun - создать тестовый прогон
/runtests - запустить тесты на стенде
/runalltests - запустить тесты на всех стендах
"""

help_acs = """Доступные команды:
/addvers - Добавить версию, если есть для нее репозитории
/getsnap - получить список снимков
/infstand stand_name - информация о стенде
/restoresnap version_name, pass_cz_server, stand_name
/createsnap version_name, pass_cz_server, stand_name
/updatestand version_name, stand_name - обновить версию ОС
/createfullsnap restore_version, new_version, pass_cz_server, stand_name - Сделать снимок
"""

################################################################################################################################################################
#ACS
################################################################################################################################################################
"""
    ACS
    Clonzilla snapshot
"""
@dp.message(Command('getsnap'))
async def get_snapshots(message: types.Message):
    res = requests.get(f"{BASE_URL}/clonezilla-snap/check-snapshots", params={'password_clonezilla_server': "team13"})
    data = res.json()
    data = data.get("snaphosts")
    snap_str = "\n".join(data)
    await message.answer(f"Ща все будет,\n{snap_str}")

@dp.message(Command("restoresnap"))
async def restore_snapshot(message: types.Message, command: CommandObject):
    if command.args is None:
        await message.reply('❌ Укажите version_name, pass_cz_server, stand_name')
        return
    version_name, pass_cs_server, stand_name = command.args.split(", ", maxsplit=3)
    res = requests.post(f"{BASE_URL}/clonezilla-snap/restore-backup", params={"version_name": version_name, 
                                                                               "password_clonezilla_server": pass_cs_server,
                                                                               "stand_name": stand_name})
    # data = res.json()
    await message.answer(f"{res.json()}")

@dp.message(Command('createsnap'))
async def create_snapshot(message: types.Message, command: CommandObject):
    if command.args is None:
        await message.reply('❌ Укажите version_name, pass_cz_server, stand_name')
        return
    version_name, pass_cs_server, stand_name = command.args.split(", ", maxsplit=3)
    res = requests.post(f"{BASE_URL}/clonezilla-snap/save-disk", params={"version_name": version_name, 
                                                                         "password_clonezilla_server": pass_cs_server, 
                                                                         "stand_name": stand_name})
    await message.answer(f"{res.text}")

"""
    ACS
    default
"""
@dp.message(Command("infstand"))
async def get_stand(message: types.Message, command: CommandObject):
    if command.args is None:
        await message.reply('❌ Укажите имя стенда')
        return
    stand_name = "".join(command.args.split(" ", maxsplit=1))
    res = requests.get(f"{BASE_URL}/stands/{stand_name}")
    data = res.json()
    answer_text = ""
    for key, value in data.items():
        if key == "id":
            continue
        answer_text += f"{key}: {value}\n"
    await message.answer(f"{answer_text}")

@dp.message(Command("addvers"))
async def add_version_and_repos(message: types.Message, command: CommandObject):
    if command.args is None:
        await message.reply('❌ Укажите версию')
        return
    version_name = "".join(command.args.split(" ", maxsplit=1))
    print(version_name, type(version_name))

    res_all_repos = requests.get("http://allta.devos.astralinux.ru/rest/api/get-repo-path-as-json").text
    data_repos = json.loads(res_all_repos)
    needed_repos = data_repos.get(version_name)

    if needed_repos:
        repos_to_one_str = "\n".join(needed_repos)
        res_ver = requests.post(f"{BASE_URL}/versions", json={"name": version_name,
                                                              "digit_name": version_name})
        data = res_ver.json()
        id_new_version = data.get("data")
        res_add_repos = requests.post(f"{BASE_URL}/repos", json={"link": repos_to_one_str,
                                                                 "version_id": id_new_version})
        
        await message.answer(f"{res_add_repos.text}")
    else:
        await message.answer("Нет репозиториев для этой версии")


@dp.message(Command("updatestand"))
async def update_stand(message: types.Message, command: CommandObject):
    if command.args is None:
        await message.reply('❌ Укажите версию и имя стенда')
        return
    version_name, stand_name = command.args.split(", ", maxsplit=2)
    # print(version_name, stand_name)
    # res_change_repos = requests.get(f"{BASE_URL}/add-tuning/change-repos", params={"version_name": version_name,
    #                                                                                "stand_name": stand_name})
    # await message.answer(f"Репы изменяются: {res_change_repos.text}")

    res_update = requests.get(f"{BASE_URL}/add-tuning/astra-update", params={"new_version": version_name,
                                                                             "stand_name": stand_name})
    await message.answer(f"Версия ОС обновляется: {res_update.text}")

@dp.message(Command("createfullsnap"))
async def create_full_snap(message: types.Message, command: CommandObject):
    if command.args is None:
        await message.reply('❌ Укажите версию и имя стенда')
        return
    pass
    restore_version, version_to_update, password_cs, stand_name = command.args.split(", ", maxsplit=4)
    res_create_full_snap = requests.post(f"{BASE_URL}/create_full_snap", params={"restore_version": restore_version,
                                                                                 "version_to_update": version_to_update,
                                                                                 "password_cs": password_cs,
                                                                                 "stand_name": stand_name})
    await message.answer(f"Полетели делать снимок {res_create_full_snap.text}")


################################################################################################################################################################
################################################################################################################################################################

def run_tests(version, stand):
    tests_dir = 'conf/main_tests_args.conf'
    releases_dir = 'conf/main_releas_args.conf'
    if stand == 'stand3':
        tests = LowServer_group
    elif stand == 'stand4':
        tests = MiddleServer_group

    with open(releases_dir, 'w') as w:
        w.write(str([version]))
    with open(tests_dir, 'w') as w:
        w.write(str(tests))

    run_command_on_stand(list(stand)[-1], http=False)


def create_test_run(version: str, final=None):
    check_len_version = version.split('.')
    if len(check_len_version) == 4 and check_len_version[3] != 'UU':
        release = '.'.join(check_len_version[:3]) 
    elif len(check_len_version) == 6 and check_len_version[3] == 'UU':
        release = '.'.join(check_len_version[:5]) 
    
    stands = ['stand3', 'stand4']
    kernels = get_kernels_from_rc(version, get_list=True)

    test_run = ZefirTestRun(use_kernels=kernels,
                            stands=stands,
                            release=release,
                            rc=version,
                            final=final)
    test_run.creater()


def update_stp(version):
    zefir_table = ZefirResultTable(test_cycle_version=str(version),
                                   token=__conf_token,
                                   basic_auth=__basic,
                                   username=__username)
    zefir_table


def acs_create_snapshot(version: str, stand):
    res_all_repos = requests.get("http://allta.devos.astralinux.ru/rest/api/get-repo-path-as-json").text
    data_repos = json.loads(res_all_repos)
    needed_repos = data_repos.get(version)
    busy_status_control(stand, 'ACS', version=version)

    if needed_repos:
        repos_to_one_str = "\n".join(needed_repos)
        res_ver = requests.post(f"{BASE_URL}/versions", json={"name": version,
                                                              "digit_name": version})
        data = res_ver.json()
        id_new_version = data.get("data")
        requests.post(f"{BASE_URL}/repos", json={"link": repos_to_one_str,
                                                 "version_id": id_new_version}) 
        
    if version.startswith('1.8'):
        restore_version = '1.8.1.6'
    elif version.startswith('1.7'):
        restore_version = '1.7.5'
    
    if stand == 'stand3':
        res_create_full_snap = requests.post(f"{BASE_URL}/create_full_snap", params={"restore_version": restore_version,
                                                                                     "version_to_update": version,
                                                                                     "password_cs": __password,
                                                                                     "stand_name": 'LowServer'})
    elif stand == 'stand4':
        res_create_full_snap = requests.post(f"{BASE_URL}/create_full_snap", params={"restore_version": restore_version,
                                                                                     "version_to_update": version,
                                                                                     "password_cs": __password,
                                                                                     "stand_name": 'MiddleServer'})
    elif stand == 'stand5':
        res_create_full_snap = requests.post(f"{BASE_URL}/create_full_snap", params={"restore_version": restore_version,
                                                                                     "version_to_update": version,
                                                                                     "password_cs": __password,
                                                                                     "stand_name": 'HighServer'})
    elif stand == 'stand10':
        res_create_full_snap = requests.post(f"{BASE_URL}/create_full_snap", params={"restore_version": restore_version,
                                                                                     "version_to_update": version,
                                                                                     "password_cs": __password,
                                                                                     "stand_name": 'LowServer2'})
    elif stand == 'stand11':
        res_create_full_snap = requests.post(f"{BASE_URL}/create_full_snap", params={"restore_version": restore_version,
                                                                                     "version_to_update": version,
                                                                                     "password_cs": __password,
                                                                                     "stand_name": 'LowServer3'})
    elif stand == 'stand12':
        res_create_full_snap = requests.post(f"{BASE_URL}/create_full_snap", params={"restore_version": restore_version,
                                                                                     "version_to_update": version,
                                                                                     "password_cs": __password,
                                                                                     "stand_name": 'LowServer4'})
    elif stand == 'stand13':
        res_create_full_snap = requests.post(f"{BASE_URL}/create_full_snap", params={"restore_version": restore_version,
                                                                                     "version_to_update": version,
                                                                                     "password_cs": __password,
                                                                                     "stand_name": 'LowServer5'})
    else: res_create_full_snap = 'Wrong stand'
    return res_create_full_snap.text


def generate_repo_path():
    pkg_path = '/dists/{}/main/binary-amd64/Packages'
    vers_path = '/dists/{}/Release'

    def sort_element(repo_list: list, element):
        [repo_list.insert(0, repo_list.pop(repo_list.index(i))) for i in repo_list if element in i]
        return repo_list[0]

    with open('./releases.json', 'r') as rj:
        links = json.load(rj)

    pkg_path_dict = {
        f"pkg_path_{key}": sort_element([f"{value.split(' ')[1]}{pkg_path}".format(value.split(' ')[2])  
        for value in links[key] if any(x in value for x in ['devel-repository', 'base-repository', 'installation'])], 'installation')
        for key in links.keys()
    }

    vers_path_dict = {
        f"vers_path_{key}": sort_element([f"{value.split(' ')[1]}{vers_path}".format(value.split(' ')[2])   
        for value in links[key] if any(x in value for x in ['devel-repository', 'base-repository', 'installation'])], 'installation')
        for key in links.keys()
    }

    return {**pkg_path_dict, **vers_path_dict}
    
    
def write_allta_conf(data):
    with open('./allta_conf.json', 'w') as w:
        json.dump(data, w, indent=4)


def add_kernels(value):
    with open('./allta_conf.json', 'r') as r:
        config = json.load(r)

    rc_kernels = get_kernels_from_rc(value, get_list=True)
    print(rc_kernels)
    config['kernels'] += [kern for kern in rc_kernels if kern not in set(config['kernels'])]
    return sorted(config['kernels'])


def update_changelog(value):
    path = './ChangeLog'
    with open(path, 'r') as r:
        version = r.readline()
        text = r.read()
    upp_version = int(version.split(' ')[2].split('.')[-1]) + 1
    pre_version = '.'.join(version.split(' ')[2].split('.')[:-1])
    new_version = f'{' '.join(version.split(' ')[:-1])} {pre_version}.{upp_version}'

    print(version)
    print(new_version)
    print('.'.join(version.split(' ')[2].split('.')[:-1]))

    commit = f'{new_version}\n* Add {value}\n\n\n\n\n'

    with open(path, 'w') as w:
        w.write(f'{commit}\n{version}{text}')


def add_testrun_folder(rc):
    main_folder = 2744
    counter = 0

    # def __create_testrun_folder(name, parentid=main_folder):
    #         add_folder_url = f'https://{JIRA_URL}/rest/tests/1.0/folder/testrun'
    #         headers = {
    #             'Authorization': __basic
    #         }
    #         data = {
    #                 "name": name,
    #                 "projectId": 11200,
    #                 "parentId": int(parentid)
    #                 }

    #         print(data)
    #         response = requests.post(add_folder_url, headers=headers, json=data)
    #         print(response.status_code)
    #         print(response.text)
    #         value = response.json()
            #config['cycle_tree_index'][name] = str(value['id'])
            #config['cycle_tree_index'] = {k: v for k, v in sorted(config['cycle_tree_index'].items())}
            #write_allta_conf(config)

    # while counter < 2:
    #     counter += 1
    #     with open('./allta_conf.json', 'r') as r:
    #         config = json.load(r)

    #     print(config['cycle_tree_index'].keys())
    #     if rc not in config['cycle_tree_index'].keys():
    #         check_len_version = rc.split('.')
    #         if len(check_len_version) == 4 and check_len_version[3] != 'UU':
    #             if '.'.join(check_len_version[:3]) in config['cycle_tree_index'].keys():
    #                 parentid = config['cycle_tree_index']['.'.join(check_len_version[:3])]
    #                 name = rc
    #                 __create_testrun_folder(name, parentid)
    #             else: __create_testrun_folder('.'.join(check_len_version[:3]))
    #         elif len(check_len_version) == 6 and check_len_version[3] == 'UU':
    #             if '.'.join(check_len_version[:5]) in config['cycle_tree_index'].keys():
    #                 parentid = config['cycle_tree_index']['.'.join(check_len_version[:5])]
    #                 name = rc
    #                 __create_testrun_folder(name, parentid)
    #             else: __create_testrun_folder('.'.join(check_len_version[:5]))
    #         else: 
    #             name = rc
    #             __create_testrun_folder(name)

    def __create_testrun_folder(name: str):
            add_folder_url = f'https://jira.astralinux.ru/rest/atm/1.0/folder'
            headers = {
                'Authorization': __basic
            }
            data = {
                    "projectKey": "BT",
                    "name": f"/stress_test/{name}",
                    "type": "TEST_RUN"
                    }

            print(data)
            response = requests.post(add_folder_url, headers=headers, json=data)
            print(response.status_code)
            print(response.text)
            value = response.json()
            print(value)
            print(f'vers {name.split('/')[-1]}')
            config['cycle_tree_index'][name.split('/')[-1]] = str(value['id'])
            config['cycle_tree_index'] = {k: v for k, v in sorted(config['cycle_tree_index'].items())}
            print(config['cycle_tree_index'])
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
                    parentfolder = '.'.join(check_len_version[:3])
                    name = rc
                    __create_testrun_folder(f'{parentfolder}/{name}')
                else: __create_testrun_folder('.'.join(check_len_version[:3]))
            elif len(check_len_version) == 6 and check_len_version[3] == 'UU':
                if '.'.join(check_len_version[:5]) in config['cycle_tree_index'].keys():
                    parentfolder = '.'.join(check_len_version[:5])
                    name = rc
                    __create_testrun_folder(f'{parentfolder}/{name}')
                else: __create_testrun_folder('.'.join(check_len_version[:5]))
            else: 
                name = rc
                __create_testrun_folder(name)


def mod_allta_conf(value, uu_value=None):
    with open('./allta_conf.json', 'r') as r:
        data = json.load(r)

    cz_name = 'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "{}" \
                -l ru_RU.UTF-8 startdisk restore {}-{}rc{} nvme0n1'

    if value not in data['releases_dict'].keys():
        if uu_value:
            data['releases_dict'][value] = uu_value
        else: data['releases_dict'][value] = value
        data['releases_dict'] = {k: v for k, v in sorted(data['releases_dict'].items())}
    if value not in data['rc_list']:
        data['rc_list'].append(value)
        data['rc_list'] = sorted(data['rc_list']) 
    if uu_value:
        if '.'.join(value.split('.')[:5]) not in data['releases_list']:
            data['releases_list'].append('.'.join(value.split('.')[:5]))
            data['releases_list'] = sorted(data['releases_list'])
    else:
        if '.'.join(value.split('.')[:3]) not in data['releases_list']:
            data['releases_list'].append('.'.join(value.split('.')[:3]))
            data['releases_list'] = sorted(data['releases_list'])
    if value not in data['release_version']:
        data['release_version'].append(value)
        data['release_version'] = sorted(data['release_version'])
    if value not in data['releases']:
        data['releases'].append(value)       
        data['releases'] = sorted(data['releases']) 
        
    for stand in stands_type['phys'].keys():
        if uu_value:
            slice_value = 5
        else: slice_value = 3
        if stand != 'stand5':
            if value not in data['cz_comm'][stand].keys():
                data['cz_comm'][stand][value] = cz_name.format(stands_ip[stand],
                                                               stands_type['phys'][stand],
                                                               ''.join(value.split('.')[:slice_value]),
                                                               ''.join(value.split('.')[slice_value:]))
        data['cz_comm'][stand] = {k: v for k, v in sorted(data['cz_comm'][stand].items())}

    write_allta_conf(data)
    
    repo = ReleaseToRepo(current_directory='.')
    repo.get_releases_index()
    repo.generate_releases_file()

    data['repo_path'] = {k: v for k, v in sorted(generate_repo_path().items())}
    write_allta_conf(data)

    data['kernels'] = add_kernels(value)
    write_allta_conf(data)

    add_testrun_folder(value)
    update_changelog(value)


def random_pics():
    random_index = randrange(len(fotos))
    return str(fotos[random_index])

def log_output(stand):
    with open(f'/home/u/git/stress_test/allta_app/conf/all_output_stand{stand}.log', 'r') as r:
        status = r.readlines()
        text = ''.join(status[:10]) + ''.join(status[-15:])
        return text
    
def status_output(stand):
    with open(f'/home/u/git/stress_test/allta_app/conf/work_status_stand{stand}.conf', 'r') as r:
        status = r.read()
        return status

def vpn_request():
    r = requests.get('https://download.vpngate.jp/en/')
    soup = BeautifulSoup(r.text, 'html.parser')
    all_vpn = soup.find_all('tr') 

    vpn_list = [str(i) for i in all_vpn]
    con_list = ['Japan', 'United States', 'Viet Nam', 'Korea Republic of', 'Romania', 'Thailand', 'Brazil', 'India', 'Cambodia']
    vpn = []

    for i in vpn_list:
        string = ''.join(i)
        vpn_dict = {}
        if 'You may connect to any' in i:
            pswd = str(i)
        try:
            for con in con_list:
                if con in string and 'L2TP/IPsec' in string:
                    vpn_dict['country'] = con
                    server = re.search('>(\\w+\\.opengw.net?)', string)
                    vpn_dict['server'] = server.group(1)
                    vpn_dict['l2tp'] = 'L2TP/IPsec'
                    vpn.append(vpn_dict)
        except Exception as e:
            #print(f'{"Type"}:{type(e).__name__}; {"Message"}:{str(e)}\n{con}')
            continue

    return '\n'.join([str(i) for i in vpn]) + '\nFor all fields paste "vpn"'



async def send_message_to_group(chat_id: str, message: str):
    await bot.send_message(chat_id, message)

async def is_file_body(path):
    if isfile(path):
        async with aiofiles.open(path, 'r') as r:
            return await r.read()
 
async def test_cycle_check():
    while True:
        try:
            stand3_results = await is_file_body(path_stand3)
            stand4_results = await is_file_body(path_stand4)
            if stand3_results:
                await send_message_to_group(chat_id, stand3_results)
                remove(path_stand3)
            elif stand4_results:
                await send_message_to_group(chat_id, stand4_results)
                remove(path_stand4)
            await asyncio.sleep(100)
        except Exception as e:
            print(str(e))
            await asyncio.sleep(100)


async def changelog_check():
    while True:
        try:
            if isfile(path_chlog):
                async with aiofiles.open(path_chlog, 'r') as r:
                    text = await r.readlines()
                    ch_text = ''.join(text[1:5]).strip()
                    vers_text = ''.join(text[:1]).strip()
            if isfile(path_tgbot_conf):
                async with aiofiles.open(path_tgbot_conf, 'r') as r:
                    chlog_text = json.loads(await r.read())
                    line1 = chlog_text['changelog']['line1']
            
            if vers_text != line1.strip():
                uphtg = '#update'
                bhtg = '#allta_update'
                upd_text = f'Вышло обновление!\n\n{vers_text}\nChangelog:\n{ch_text}\n\n{uphtg}\n{bhtg}'
                await send_message_to_group(chat_id, upd_text)
                chlog_text['changelog']['line1'] = vers_text
                async with aiofiles.open(path_tgbot_conf, 'w') as w:
                    await w.write(json.dumps(chlog_text, indent=4))
            await asyncio.sleep(100)
        except Exception as e:
            print(str(e))
            await asyncio.sleep(100)


@dp.message(Command('id'))
async def get_my_updates(message: types.Message):
    chat_id = message.chat.id
    print(f'Chat ID: {chat_id}')
    await message.answer(f'Chat ID: {chat_id}')

@dp.message(Command('start'))
async def start(message: types.Message):
    await bot.send_photo(chat_id=message.chat.id, 
                             photo='https://ichip.ru/images/cache/2022/8/7/q90_627312_9aeaad893dd578d3855844439.png', 
                             caption='Привет! \nНапиши мне help, если нужна помощь', 
                             reply_to_message_id=message.message_id)

@dp.message(Command('vpn'))
async def vpn(message: types.Message):
    await message.reply(str(vpn_request()))

@dp.message(Command('log'))
async def log_server(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text='LowServer', callback_data='LowServer_log'),
                types.InlineKeyboardButton(text='MiddleServer', callback_data='MiddleServer_log'))
    await message.reply('Какой сервер интересует?', reply_markup=builder.as_markup())

@dp.callback_query(lambda c: c.data in ['LowServer_log', 'MiddleServer_log'])
async def process_callback(query: types.CallbackQuery):
    if query.data == 'LowServer_log':
        server_id = '3'
        choose = 'Выбран LowServer:\nСтатус:'
    elif query.data == 'MiddleServer_log':
        server_id = '4'
        choose = 'Выбран MiddleServer:\nСтатус:'
    await query.message.reply(f'{choose} {log_output(server_id)}')
    await bot.edit_message_reply_markup(query.message.chat.id, query.message.message_id)


@dp.message(Command('status'))
async def status_server(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text='LowServer', callback_data='LowServer_status'),
                types.InlineKeyboardButton(text='MiddleServer', callback_data='MiddleServer_status'))
    await message.reply('Какой сервер интересует?', reply_markup=builder.as_markup())

@dp.callback_query(lambda c: c.data in ['LowServer_status', 'MiddleServer_status'])
async def process_callback(query: types.CallbackQuery):
    if query.data == 'LowServer_status':
        server_id = '3'
        choose = 'Выбран LowServer:\nСтатус:'
    elif query.data == 'MiddleServer_status':
        server_id = '4'
        choose = 'Выбран MiddleServer:\nСтатус:'
    await query.message.reply(f'{choose} {status_output(server_id)}')
    await bot.edit_message_reply_markup(query.message.chat.id, query.message.message_id)


@dp.message(Command('addrc'))
async def addrc(message: types.Message, command: CommandObject):
    rc = None
    password = None
    if command.args is None:
        await message.reply('❌ Укажите версию RC и пароль')
        return
    try:
        rc, password = command.args.split(' ', maxsplit=1)
    except ValueError:
        content = Text('❌ Укажите версию RC, пароль. Пример:\n'
                            '/addrc <RC> <password>')
        await message.reply(**content.as_kwargs())
        return
    if password == __password:
        await message.reply(f'✅ Доступ разрешен\nДобавляю новую конфигурацию RC: {rc}')
        mod_allta_conf(rc)
        #stand3, stand4 = acs_create_snapshot(rc)
        content = f'Пользователь: "{message.from_user.full_name}"\nID: "{message.from_user.id}"\n\nДействие:\nДобавлена конфигурация RC: "{rc}"'
        await bot.send_message(chat_id=chat_id, text=content, parse_mode=None)
        #await bot.send_message(chat_id=chat_id, text=f'Запуск обновления LowServer: {stand3}', parse_mode=None)
        #await bot.send_message(chat_id=chat_id, text=f'Запуск обновления MiddleServer: {stand4}', parse_mode=None)
    else: 
        content = Text(f'Доступ запрещен:\n❌ ', {message.from_user.full_name})
        await message.reply(**content.as_kwargs())


@dp.message(Command('adduurc'))
async def addrc(message: types.Message, command: CommandObject):
    rc = None
    password = None
    uu_value = None #Использовать только если UU, иначе игнорировать
    if command.args is None:
        await message.reply('❌ Укажите версию RC и пароль')
        return
    try:
        rc, password, uu_value = command.args.split(' ', maxsplit=2)
    except ValueError:
        content = Text('❌ Укажите версию RC, пароль и UU build version при наличии. Пример:\n'
                            '/adduurc <RC> <password> <UU build version>')
        await message.reply(**content.as_kwargs())
        return
    if password == __password:
        await message.reply(f'✅ Доступ разрешен\nДобавляю новую конфигурацию RC: {rc}')
        mod_allta_conf(rc, uu_value)
        #stand3, stand4 = acs_create_snapshot(rc)
        content = f'Пользователь: "{message.from_user.full_name}"\nID: "{message.from_user.id}"\n\nДействие:\nДобавлена конфигурация RC: "{rc}"'
        await bot.send_message(chat_id=chat_id, text=content, parse_mode=None)
        #await bot.send_message(chat_id=chat_id, text=f'Запуск обновления LowServer: {stand3}', parse_mode=None)
        #await bot.send_message(chat_id=chat_id, text=f'Запуск обновления MiddleServer: {stand4}', parse_mode=None)
    else: 
        content = Text(f'Доступ запрещен:\n❌ ', {message.from_user.full_name})
        await message.reply(**content.as_kwargs())


@dp.message(Command('acs'))
async def addrc(message: types.Message, command: CommandObject):
    rc = None
    stand = None
    password = None
    if command.args is None:
        await message.reply('❌ Укажите версию RC, стенд и пароль')
        return
    try:
        rc, stand, password = command.args.split(' ', maxsplit=2)
    except ValueError:
        content = Text('❌ Укажите версию RC, стенд и пароль. Пример:\n'
                            '/acs <RC> <stand#> <password>')
        await message.reply(**content.as_kwargs())
        return
    if password == __password:
        await message.reply(f'✅ Доступ разрешен\nСоздаю снимок: {rc}')
        if stand == 'stand3':
            server = 'LowServer'
        elif stand == 'stand4':
            server = 'MiddleServer'
        elif stand == 'stand5':
            server = 'HighServer'
        
        stand_resp = acs_create_snapshot(rc, stand)
        content = f'Пользователь: "{message.from_user.full_name}"\nID: "{message.from_user.id}"\n\nДействие:\nСоздание снимка: "{rc}"'
        await bot.send_message(chat_id=chat_id, text=content, parse_mode=None)
        await bot.send_message(chat_id=chat_id, text=f'Запуск ACS на {server}: {stand_resp}', parse_mode=None)
    else: 
        content = Text(f'Доступ запрещен:\n❌ ', {message.from_user.full_name})
        await message.reply(**content.as_kwargs())


@dp.message(Command('update_stp'))
async def addrc(message: types.Message, command: CommandObject):
    rc = None
    password = None
    if command.args is None:
        await message.reply('❌ Укажите версию RC и пароль')
        return
    try:
        rc, password = command.args.split(' ', maxsplit=1)
    except ValueError:
        content = Text('❌ Укажите версию RC и пароль. Пример:\n'
                            '/update_stp <RC> <password>')
        await message.reply(**content.as_kwargs())
        return
    if password == __password:
        await message.reply(f'✅ Доступ разрешен\nОбновляю СТП: {rc}')
        update_stp(rc)
        content = f'Пользователь: "{message.from_user.full_name}"\nID: "{message.from_user.id}"\n\nДействие:\nОбновление СТП: "{rc}"'
        await bot.send_message(chat_id=chat_id, text=content, parse_mode=None)
    else: 
        content = Text(f'Доступ запрещен:\n❌ ', {message.from_user.full_name})
        await message.reply(**content.as_kwargs())


@dp.message(Command('add_testrun'))
async def addrc(message: types.Message, command: CommandObject):
    rc = None
    password = None
    final = None
    if command.args is None:
        await message.reply('❌ Укажите версию RC и пароль')
        return
    try:
        rc, password, final = command.args.split(' ', maxsplit=2)
    except ValueError:
        content = Text('❌ Укажите версию RC и пароль. Пример:\n'
                            '/add_testrun <RC> <password> <final>')
        await message.reply(**content.as_kwargs())
        return
    if password == __password:
        await message.reply(f'✅ Доступ разрешен\nСоздаю тестовый прогон: {rc}')
        if final == 'final':
            create_test_run(rc, final=True)
        else: create_test_run(rc)
        content = f'Пользователь: "{message.from_user.full_name}"\nID: "{message.from_user.id}"\n\nДействие:\nСоздан тестовый прогон: "{rc}"'
        await bot.send_message(chat_id=chat_id, text=content, parse_mode=None)
    else: 
        content = Text(f'Доступ запрещен:\n❌ ', {message.from_user.full_name})
        await message.reply(**content.as_kwargs())


@dp.message(Command('runtests'))
async def addrc(message: types.Message, command: CommandObject):
    rc = None
    stand = None
    password = None
    if command.args is None:
        await message.reply('❌ Укажите версию RC, стенд и пароль')
        return
    try:
        rc, stand, password = command.args.split(' ', maxsplit=2)
    except ValueError:
        content = Text('❌ Укажите версию RC, стенд и пароль. Пример:\n'
                            '/runtests <RC> <stand#> <password>')
        await message.reply(**content.as_kwargs())
        return
    if password == __password:
        await message.reply(f'✅ Доступ разрешен\nЗапускаю тесты: {rc}')   
        run_tests(rc, stand)
        content = f'Пользователь: "{message.from_user.full_name}"\nID: "{message.from_user.id}"\n\nДействие:\nЗапуск тестов: "{rc}" - "{stand}"'
        await bot.send_message(chat_id=chat_id, text=content, parse_mode=None)
    else: 
        content = Text(f'Доступ запрещен:\n❌ ', {message.from_user.full_name})
        await message.reply(**content.as_kwargs())


@dp.message(Command('runalltests'))
async def addrc(message: types.Message, command: CommandObject):
    rc = None
    password = None
    if command.args is None:
        await message.reply('❌ Укажите версию RC и пароль')
        return
    try:
        rc, password = command.args.split(' ', maxsplit=1)
    except ValueError:
        content = Text('❌ Укажите версию RC и пароль. Пример:\n'
                            '/runtests <RC> <password>')
        await message.reply(**content.as_kwargs())
        return
    if password == __password:
        await message.reply(f'✅ Доступ разрешен\nЗапускаю тесты: {rc}')   
        run_tests(rc, 'stand3')
        sleep(1)
        run_tests(rc, 'stand4')
        content = f'Пользователь: "{message.from_user.full_name}"\nID: "{message.from_user.id}"\n\nДействие:\nЗапуск тестов: "{rc}" - "All stands"'
        await bot.send_message(chat_id=chat_id, text=content, parse_mode=None)
    else: 
        content = Text(f'Доступ запрещен:\n❌ ', {message.from_user.full_name})
        await message.reply(**content.as_kwargs())



@dp.message(F.text)
async def get_message(message: types.Message):
    if "Allta" in message.text: 
        if "help acs" in message.text.lower():
            await message.reply(help_acs)
        elif "help" in message.text.lower():
            await message.reply(help_text)
        else: 
            await bot.send_photo(chat_id=message.chat.id, 
                                photo='https://thumbs.dreamstime.com/z/%D0%BF%D0%B8%D0%BD%D0%B3%D0%B2%D0%B8%D0%BD-%D0%B2-%D0%BA%D1%80%D0%B0%D1%81%D0%BD%D0%BE%D0%BC-%D1%88%D0%B0%D1%80%D1%84%D0%B5-%D0%BC%D0%B8%D0%BB%D1%8B%D0%B9-%D0%B8%D0%B7-%D0%BC%D1%83%D0%BB%D1%8C%D1%82%D1%84%D0%B8%D0%BB%D1%8C%D0%BC%D0%B0-%D1%81-%D0%B1%D0%BE%D0%BB%D1%8C%D1%88%D0%B8%D0%BC%D0%B8-%D0%B3%D0%BB%D0%B0%D0%B7%D0%B0%D0%BC%D0%B8-231085136.jpg', 
                                caption='Oops! Команда не идентифицирована.\nНапиши мне help, если нужна помощь', 
                                reply_to_message_id=message.message_id)
    elif message.reply_to_message and message.reply_to_message.from_user:
        if message.reply_to_message.from_user.username == "ALLTAbot":
            if "help acs" in message.text.lower():
                await message.reply(help_acs)
            elif "help" in message.text.lower():
                await message.reply(help_text)
            else: 
                await bot.send_photo(chat_id=message.chat.id, 
                                    photo='https://thumbs.dreamstime.com/z/%D0%BF%D0%B8%D0%BD%D0%B3%D0%B2%D0%B8%D0%BD-%D0%B2-%D0%BA%D1%80%D0%B0%D1%81%D0%BD%D0%BE%D0%BC-%D1%88%D0%B0%D1%80%D1%84%D0%B5-%D0%BC%D0%B8%D0%BB%D1%8B%D0%B9-%D0%B8%D0%B7-%D0%BC%D1%83%D0%BB%D1%8C%D1%82%D1%84%D0%B8%D0%BB%D1%8C%D0%BC%D0%B0-%D1%81-%D0%B1%D0%BE%D0%BB%D1%8C%D1%88%D0%B8%D0%BC%D0%B8-%D0%B3%D0%BB%D0%B0%D0%B7%D0%B0%D0%BC%D0%B8-231085136.jpg', 
                                    caption='Oops! Команда не идентифицирована.\nНапиши мне help, если нужна помощь', 
                                    reply_to_message_id=message.message_id)



async def main():
    task1 = asyncio.create_task(test_cycle_check())
    task2 = asyncio.create_task(changelog_check())
    task3 = asyncio.create_task(dp.start_polling(bot))

    await asyncio.gather(task1, task2, task3)

if __name__ == "__main__":
    asyncio.run(main())