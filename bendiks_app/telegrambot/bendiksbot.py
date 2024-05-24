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



with open('/home/u/key.conf', 'r') as r:
    API_TOKEN = r.read().strip()
bot = Bot(API_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()

path_chlog = '/home/u/git/stress_test/bendiks_app/ChangeLog'
path_tgbot_conf = '/home/u/telegrambotconf.json'
path_stand3 = '/home/u/git/stress_test/bendiks_app/telegrambot/results_stand3.txt'
path_stand4 = '/home/u/git/stress_test/bendiks_app/telegrambot/results_stand4.txt'
chat_id = '-1002121821530'
fotos = [
'https://sun9-24.userapi.com/impg/IZ8aU4agpRfx6mw2oPo8GodBU_XvtKiwe-FeUA/mjmDRavXZPs.jpg?size=1280x1119&quality=95&sign=\
23214073b39fc706737d33e7ade4de5b&c_uniq_tag=_KqT8ouMY-LQeWE7W33O-vlbD3h8PbAHULqbk5uvbQg&type=album',
'https://avatars.mds.yandex.net/i?id=2f27371e02e01ea034624aa4a1e7eb06-4298968-images-thumbs&ref=rim&n=33&w=262&h=200',
'https://sun9-17.userapi.com/impg/zdR1_joV0_O6Xnba-bfkt8DyCEZqH6VD8s8RAw/tGohYNLhvlk.jpg?size=736x1472&quality=95&sign=\
4cb1af85ac687db37f3f092f8c1d9e35&c_uniq_tag=NSqjmxZdyHmUP31opyfHcISGDWTVKGNVzN6TbWQ-54Q&type=album',
'https://sun9-38.userapi.com/impg/REykA5Xgo5DNHj2aQdnKhdW7i6v1Gcae4BwsHA/SvzF-I9yHxU.jpg?size=752x1222&quality=96&sign=\
63e981b3b1d9f96e861dab158190c314&c_uniq_tag=LC62h939btYyDEUpTwBfnzEcFfg7yAJGoOdKhHVqqcg&type=album'
]

help_text = """Доступные команды:
/log - получить прогресс выполнения прогона
/status - узнать статус прогона
/id - узнать ID чата
/vpn - доступные для использования в телефоне найстройки VPN'
/addrc - добавить новую версию релиз кандидата
"""

def write_bendiks_conf(data):
    with open('./bendiks_conf.json', 'w') as w:
        json.dump(data, w, indent=4)


def update_changelog(value):
    path = '../ChangeLog'
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


def mod_bendiks_conf(value):
    with open('../bendiks_conf.json', 'r') as r:
        data = json.load(r)

    stands = {'LowServer':'10.177.103.204',
              'MiddleServer':'10.177.103.203'}
    cz_name = 'sudo drbl-ocs -g auto -e1 auto -e2 -r -x -j2 -k0 -sc0 -p reboot -h "{}" \
                -l ru_RU.UTF-8 startdisk restore {}-{}rc{} nvme0n1'

    if value not in data['releases_dict'].keys():
        data['releases_dict'][value] = value
    if value not in data['rc_list']:
        data['rc_list'].append(value)
    if '.'.join(value.split('.')[:3]) not in data['releases_list']:
        data['releases_list'].append('.'.join(value.split('.')[:3]))
    if value not in data['release_version']:
        data['release_version'].append(value)
    if value not in data['releases']:
        data['releases'].append(value)        

    if value not in data['cz_comm']['stand3'].keys():
        data['cz_comm']['stand3'][value] = cz_name.format(stands['LowServer'],
                                                          'LowServer',
                                                          ''.join(value.split('.')[:3]),
                                                          ''.join(value.split('.')[3:]))
    if value not in data['cz_comm']['stand4'].keys():
        data['cz_comm']['stand4'][value] = cz_name.format(stands['MiddleServer'],
                                                          'MiddleServer',
                                                          ''.join(value.split('.')[:3]),
                                                          ''.join(value.split('.')[3:]))
    write_bendiks_conf(data)
    update_changelog(value)


def random_pics():
    random_index = randrange(len(fotos))
    return str(fotos[random_index])

def log_output(stand):
    with open(f'/home/u/git/stress_test/bendiks_app/conf/all_output_stand{stand}.log', 'r') as r:
        status = r.readlines()
        text = ''.join(status[:10]) + ''.join(status[-15:])
        return text
    
def status_output(stand):
    with open(f'/home/u/git/stress_test/bendiks_app/conf/work_status_stand{stand}.conf', 'r') as r:
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
                bhtg = '#Bendiks_update'
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
        await message.reply('Error: Укажите версию RC и пароль')
        return
    try:
        rc, password = command.args.split(' ', maxsplit=1)
    except ValueError:
        content = Text('Error: Укажите версию RC и пароль. Пример:\n'
                            '/addrc <RC> <password>')
        await message.reply(**content.as_kwargs())
        return
    if password == 'bendik$':
        await message.reply(f'✅ Доступ разрешен\nДобавляю новую версию RC: {rc}')
        mod_bendiks_conf(rc)
    else: 
        content = Text(f'Доступ запрещен:\n❌ ', {message.from_user.full_name})
        await message.reply(**content.as_kwargs())



@dp.message(F.text)
async def get_message(message: types.Message):
    if "Bendiks" in message.text: 
        if "help" in message.text.lower():
            await message.reply(help_text)
        else: 
            await bot.send_photo(chat_id=message.chat.id, 
                                photo=random_pics(), 
                                caption='Oops! Команда не идентифицирована.\nНапиши мне help, если нужна помощь', 
                                reply_to_message_id=message.message_id)
    elif message.reply_to_message and message.reply_to_message.from_user:
        if message.reply_to_message.from_user.username == "BendiksDEVQAbot":
            if 'help' in message.text.lower():
                await message.reply(help_text)
            else: 
                await bot.send_photo(chat_id=message.chat.id, 
                                    photo=random_pics(), 
                                    caption='Oops! Команда не идентифицирована.\nНапиши мне help, если нужна помощь', 
                                    reply_to_message_id=message.message_id)
        





async def main():
    task1 = asyncio.create_task(test_cycle_check())
    task2 = asyncio.create_task(changelog_check())
    task3 = asyncio.create_task(dp.start_polling(bot))

    await asyncio.gather(task1, task2, task3)

if __name__ == "__main__":
    asyncio.run(main())