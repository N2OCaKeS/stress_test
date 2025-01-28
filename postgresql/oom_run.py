import requests
import json
from balance.bl_lib import bl
from oom.oom_lib import VBox
import argparse


parser = argparse.ArgumentParser()
parser.add_argument('-vbox', '--set-vbox',
                    action='store',
                    required=True,
                    help='set-vbox to vm',
                    dest='SET_BOX')


"""
VARIABLES
"""

provider = VBox
args = parser.parse_args()
astra_config_url = 'http://allta.devos.astralinux.ru/rest/api/get-box-config'
response_ac = requests.get(astra_config_url)
if response_ac.status_code == 200:
    with open('box-config.json', 'wb') as acb:
        acb.write(response_ac.content)
else:
    print(f'Failed to get file from {astra_config_url}: {response_ac.status_code}')

with open('box-config.json', 'r') as r:
    dates = json.loads(r.read())


box_name, box_url = bl.box_wrapper(args.SET_BOX, dates)

provider.prepare()
provider.build(box_name=box_name, box_url=box_url, kernel='kernel', rc=args.SET_BOX)