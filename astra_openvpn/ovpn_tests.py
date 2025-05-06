import json
import os
from ovpn_conf import BOXES, DATES
from libs.libovpn import run_command
import subprocess
from allta import SystemCommands

class AOvpn20kTest:
    def __init__(self, boxes=BOXES, dates=DATES):
        self.boxes = boxes
        self.dates = dates


    # check version + check mod to build vm from "dates file"
    def choose_box(self):
        result = ""

        with open(self.dates, "r", encoding="UTF-8") as fd:
            fd = fd.read()
            all_text = fd.lower()
            fd = fd.split()
            count_v = -1 * len([v for v in fd if v.startswith("1.7.")])
            count_v += len([v for v in fd if v.startswith("1.8.")])
            print(count_v)


            with open(self.boxes, "r", encoding="UTF-8") as fb:
                temp = json.load(fb)
                for box in temp["vagrant_box"]:
                    for k, v in box.items():
                        if count_v > 0 and k.startswith("1.8.1.") or count_v < 0 and k.startswith("1.7.5."):
                            url = v[1]
                            print(url)
                            if "orel" in all_text and url.endswith("o.box"):
                                result = url
                            elif "smolensk" in all_text and url.endswith("s.box"):
                                result = url
                            elif "voronezh" in all_text and url.endswith("v.box"):
                                result = url     
        return result



sys_com = SystemCommands()   
test = AOvpn20kTest()
sys_com.cmd("wget " + test.choose_box())
box = test.choose_box().split('/')[-1].replace(".box", "")
os.environ['UPDATE'] = box

sys_com.cmd("vagrant mutate 1.*.box libvirt")
#sys_com.cmd("VAGRANT_LOG=debug vagrant up --provider libvirt 2>&1 | tee vagrant.log")
sys_com.cmd("vagrant up --provider=libvirt")
