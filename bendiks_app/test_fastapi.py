from fastapi import FastAPI
from fastapi import Form, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi import HTTPException
from time import sleep
import socket
import json
import psycopg2
from libs.zefir import ZefirResultTable
from starlette.responses import PlainTextResponse
from starlette.responses import RedirectResponse
from starlette.exceptions import HTTPException
from libs.libbend import (index_page,
                          BackgroundTasks,
                          run_command_on_stand,
                          ssh_command,
                          background_task_main,
                          background_task_brest,
                          background_stat_storage_main,
                          update_settings_block,
                          get_kernels_from_rc,
                          main_url,
                          mobile_url,
                          brest_url,
                          up,
                          bp, 
                          psyc,
                          stands_ip,
                          user_app)

app = FastAPI()
templates = Jinja2Templates(directory='templates')
app.mount("/static", StaticFiles(directory="static"), name="static")

with open('/home/u/tokens.json', 'r') as r:
    tokens = json.load(r)
__conf_token = tokens['conf_token']
__username = tokens['username']
__jira_token = tokens['jira_token']

thread_task_main = BackgroundTasks(background_task_main)
thread_storage_main = BackgroundTasks(background_stat_storage_main)
thread_task_main.run()
thread_storage_main.run()

@app.get("/update_cpumeminfo")
async def restart_cpumeminfo():
    thread_task_main.cancel()
    thread_storage_main.cancel()
    sleep(1)
    thread_task_main.run()
    thread_storage_main.run()
    return PlainTextResponse(content='update successful', status_code=200)


@app.get("/")
def redirect_login():
    return RedirectResponse(url='/login')

@app.exception_handler(500)
async def internal_server_error(request, exc):
    return RedirectResponse(url='/login')

@app.exception_handler(404)
async def page_not_found(request, exc):
    return RedirectResponse(url='/login')


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    if username == user_app and password == str(up).replace('\n', '').replace('\r', ''):
        return RedirectResponse(url=f'/{main_url}')
    elif username == user_app and password == str(bp).replace('\n', '').replace('\r', ''):
        return RedirectResponse(url=f'/{brest_url}')  
    else:
        return templates.TemplateResponse('login.html', {'request': request, 'error': 'Неверный логин или пароль'})

@app.get('/login')
def get_login(req: Request):
    return templates.TemplateResponse('login.html', {'request': req})

@app.get(f'/{mobile_url}')
@app.post(f'/{mobile_url}')
async def index_mobile():
    return await index_page('mobile')

@app.get(f'/{main_url}')
@app.post(f'/{main_url}')
async def index_main():
    return await index_page('main')

# @app.get(f'/{brest_url}')
# @app.post(f'/{brest_url}')
# async def index_brest():
#     return index_page('brest')

# @app.post('/run-command-stand{num}')
# async def run_command(num):
#     return run_command_on_stand(num)


# @app.get('/update/{version}')
# async def update_stp(version):
#     """
#     Обновить состав тестового прогона
#     """
#     zefir_table = ZefirResultTable(test_cycle_version=str(version),
#                                    token=__conf_token,
#                                    basic_auth=__jira_token,
#                                    username=__username)
#     zefir_table

#     return index_page('main')


# @app.get('/api/load_info/{stand}')
# async def get_load_info(stand):
#     conn = psycopg2.connect(
#                             host=psyc['host'],
#                             database=psyc['database'],
#                             user=psyc['user'],
#                             password=psyc['password']
#                             )

#     id = 1
#     cursor = conn.cursor()
#     select_query = f"SELECT {stand}_cpu, {stand}_cpu_user, {stand}_cpu_system, {stand}_ram, {stand}_nvme, {stand}_sda, {stand}_temp_cpu FROM main_table WHERE id = %s"
#     cursor.execute(select_query, [id])

#     result = cursor.fetchone()
#     if result is not None:
#         load_cpu = result[0]
#         load_cpu_user = result[1]
#         load_cpu_system = result[2]
#         temp_cpu = result[6]
#         load_ram = result[3]
#         load_nvme = result[4]
#         load_sda = result[5]
#     else:
#         load_cpu = '-'
#         load_cpu_user = '-'
#         load_cpu_system = '-'
#         temp_cpu = '-'
#         load_ram = '-'
#         load_nvme = '-'
#         load_sda = '-'

#     cursor.close()
#     conn.close()
    
#     return {
#         f"load_cpu_{stand}":load_cpu,
#         f"load_cpu_user_{stand}":load_cpu_user,
#         f"load_cpu_system_{stand}":load_cpu_system,
#         f"temp_cpu_{stand}":temp_cpu,
#         f"load_ram_{stand}":load_ram,
#         f"load_nvme_{stand}":load_nvme,
#         f"load_sda_{stand}":load_sda
#     } 


# @app.get('/update_page_info_{page}')
# async def update(page: str):
#     return index_page(f'{page}', ajax=True)


# @app.get("/update_power_status/{stand}")
# async def check_running_system(stand: str):
#     sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#     sock.settimeout(0.5)   
#     try:
#         result = sock.connect_ex((stands_ip[stand], 22))
#         if result == 0:
#             return {"is_running": True}
#         else: 
#             return {"is_running": False}
#     except socket.timeout:
#         return {"is_running": False}
#     except Exception as e:
#         print(e)
#         return {"is_running": False}
#     finally:
#         sock.close()


# @app.post('/reboot/{stand}')
# async def reboot(stand):
#     ssh_command('sudo reboot', 
#                 stand_ip=stands_ip[stand])
   
# @app.post('/poweroff/{stand}')
# async def poweroff(stand):
#     ssh_command('sudo poweroff', 
#                 stand_ip=stands_ip[stand])


# @app.get('/update_block_{part}')
# @app.post('/update_block_{part}')
# def update_block(part):
#     if part == 'components':
#         return update_settings_block()
#     else:
#         return get_kernels_from_rc(part)
    
