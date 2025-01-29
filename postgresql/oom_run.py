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

parser.add_argument('-tcyc', '--test-cycle-name',
                    action='store',
                    required=True,
                    help='test-cycle-name',
                    dest='TCYC')


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


#kernel = str(args.TCYC).split('_')[2]
box_name, box_url = bl.box_wrapper(args.SET_BOX, dates)





provider.prepare()
provider.build(box_name=box_name, box_url=box_url, kernel=args.TCYC, rc=args.SET_BOX)
provider.check()