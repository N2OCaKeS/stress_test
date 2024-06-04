import json
import requests

from aiogram import F, Router
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.types import Message, CallbackQuery

import app.keyboards as kb
from config import BASE_URL


router = Router()

HELP_TEXT = """Доступные команды:
/addvers - Добавит версию, если есть для нее репозитории
/getsnap - получить список снимков
/infstand stand_name - информация о стенде
/restoresnap version_name, pass_cz_server, stand_name
/createsnap version_name, pass_cz_server, stand_name
/updatestand version_name, stand_name - обновит версию ОС
/createfullsnap restore_version, new_version, pass_cz_server, stand_name - Сделать снимок
"""

@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.reply(f"Привет!\nТвой ID: {message.from_user.id}\nИмя: {message.from_user.first_name}")

@router.message(Command("help"))
async def get_help(message: Message):
    await message.answer(HELP_TEXT)

@router.message(F.text == "Как дела?")
async def how_are_you(message: Message):
    await message.answer("ОК!")


"""
    ACS
    Clonzilla snapshot
"""
@router.message(Command('getsnap'))
async def get_snapshots(message: Message):
    res = requests.get(f"{BASE_URL}/clonezilla-snap/check-snapshots", params={'password_clonezilla_server': "team13"})
    data = res.json()
    data = data.get("snaphosts")
    snap_str = "\n".join(data)
    await message.answer(f"Ща все будет,\n{snap_str}")

@router.message(Command("restoresnap"))
async def restore_snapshot(message: Message, command: CommandObject):
    if command.args is None:
        await message.reply('❌ Укажите version_name, pass_cz_server, stand_name')
        return
    version_name, pass_cs_server, stand_name = command.args.split(", ", maxsplit=3)
    res = requests.post(f"{BASE_URL}/clonezilla-snap/restore-backup", params={"version_name": version_name, 
                                                                               "password_clonezilla_server": pass_cs_server,
                                                                               "stand_name": stand_name})
    # data = res.json()
    await message.answer(f"{res.json()}")

@router.message(Command('createsnap'))
async def create_snapshot(message: Message, command: CommandObject):
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
@router.message(Command("infstand"))
async def get_stand(message: Message, command: CommandObject):
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

@router.message(Command("addvers"))
async def add_version_and_repos(message: Message, command: CommandObject):
    if command.args is None:
        await message.reply('❌ Укажите версию')
        return
    version_name = "".join(command.args.split(" ", maxsplit=1))
    print(version_name, type(version_name))

    res_all_repos = requests.get("http://bendiks.devos.astralinux.ru/rest/api/get-repo-path-as-json").text
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


@router.message(Command("updatestand"))
async def update_stand(message: Message, command: CommandObject):
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

@router.message(Command("createfullsnap"))
async def create_full_snap(message: Message, command: CommandObject):
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