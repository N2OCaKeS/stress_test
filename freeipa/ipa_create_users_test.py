import ipalib
import sys
from ipalib import api
from threading import Thread

api.bootstrap(context='server')
api.finalize()
api.activate()

def create_user(login):
    try:
        result = api.Command['user_add'](login, givenname='Test', sn=f'User{login}')
        print(f"Created {login}: {result}")
    except Exception as e:
        print(f"Error {login}: {e}")

threads = []
for i in range(100):
    t = Thread(target=create_user, args=(f'user{i}',))
    t.start()
    threads.append(t)
for t in threads:
    t.join()