#!/home/u/python/Python-3.12.1/venv/bin/python3.12
import string
import random
import subprocess
import os
from libs.liballta import get_aqs_json, ReleaseToRepo
import json


with open('/home/u/tokens.json', 'r') as r:
    tokens = json.load(r)
__git_token = tokens['git_token']
current_directory = os.getcwd()
get_aqs_json(current_directory, __git_token)


repo = ReleaseToRepo(current_directory=current_directory)
repo.get_releases_index()
repo.generate_releases_file()


def generate_random_string(length):
    letters_and_digits = string.ascii_letters + string.digits
    rand_string = ''.join(random.sample(letters_and_digits, length))
    return rand_string * 5


with open('/home/u/url', 'w') as w:
    w.write(generate_random_string(40))
with open('/home/u/url_mob', 'w') as w:
    w.write(generate_random_string(40))
with open('/home/u/url_brest', 'w') as w:
    w.write(generate_random_string(40))


subprocess.run('sudo systemctl daemon-reload', shell=True)
subprocess.run('sudo systemctl restart allta.service', shell=True)
subprocess.run('sudo systemctl restart nginx.service', shell=True)
subprocess.run('sudo systemctl restart bot_allta.service', shell=True)
subprocess.run('sudo systemctl restart acs.service', shell=True)
subprocess.run('sudo systemctl restart statistics.service', shell=True)
subprocess.run('sudo systemctl restart allta_infocollector.service', shell=True)
