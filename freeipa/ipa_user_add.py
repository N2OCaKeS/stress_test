# pip3 install python-freeipa

import time
import subprocess
from python_freeipa import ClientMeta
from ipa_conf import MAX_USERS_AUTH, USERS_AUTH_STEP

# hostname = 'stand-1-i711700-32-low.stress-testing.local'
hostname = subprocess.run('hostname', 
                          shell=True, 
                          stdout=subprocess.PIPE, 
                          encoding='utf-8').stdout.strip('\n')
admin_username = 'admin'
admin_password = '12345678'

client = ClientMeta(hostname, verify_ssl=False)

client.login(admin_username, admin_password)

time_start_create_users = time.time()

for id in range(0, MAX_USERS_AUTH + USERS_AUTH_STEP):
    response = client.user_add(
        a_uid=f'user{id}',
        o_givenname='User',
        o_sn=f'{id}',
        o_cn=f'User {id}',  
        o_userpassword='password',
        o_mail=f'user{id}@testdom.local',
        o_no_members=True
   )

time_end_create_users = time.time() - time_start_create_users

print(f"На создание пользователей ушло: {time_end_create_users}")