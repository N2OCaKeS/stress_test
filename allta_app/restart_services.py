#!/home/u/python/Python-3.12.1/venv/bin/python3.12
import string
import random
import subprocess
import os

from allta_image_conf import tokens
from libs.liballta import get_aqs_json, ReleaseToRepo



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
#subprocess.run('sudo systemctl restart bot_allta.service', shell=True)
subprocess.run('sudo systemctl restart acs.service', shell=True)
subprocess.run('sudo systemctl restart statistics.service', shell=True)

subprocess.run('cd /home/u/folder_git_for_infocollector && python3 git_clone.py', shell=True)
subprocess.run('cd /home/u/folder_git_for_infocollector/stress_test && git checkout allta_infocollector', shell=True)
subprocess.run('python3 /home/u/folder_git_for_infocollector/stress_test/allta_infocollector/config_handler.py', shell=True)
subprocess.run('sleep 10 && sudo systemctl restart allta_infocollector.service', shell=True)

#subprocess.run('cd /home/u/folder_git_for_libs && python3 git_clone.py', shell=True)
#subprocess.run('cd /home/u/folder_git_for_libs/stress_test && git checkout libs', shell=True)
#subprocess.run('systemctl stop devpi', shell=True)
#subprocess.run('bash /home/u/folder_git_for_libs/stress_test/libs/devpi_service/install_service.sh', shell=True)
#subprocess.run('sudo systemctl restart devpi', shell=True)

subprocess.run('sudo docker image ls', shell=True)
subprocess.run('sudo docker image prune', shell=True)
subprocess.run('sudo docker image ls', shell=True)


conf_file_path = 'conf/needrefresh.conf'
with open(conf_file_path, 'w') as f:
    f.write('True')
