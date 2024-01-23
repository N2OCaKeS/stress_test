#!/home/u/python/Python-3.12.1/venv/bin/python3.12
import string
import random
import subprocess


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
subprocess.run('sudo systemctl restart bendiks.service', shell=True)
subprocess.run('sudo systemctl restart nginx.service', shell=True)

