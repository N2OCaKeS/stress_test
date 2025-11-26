import os
from os.path import exists

from exb_conf import REPORT_PATH

class Report:
    def __init__(self, report_path=REPORT_PATH, img_width=16.256, img_height=12.192):
        self.report_path = report_path
        self.width = img_width
        self.height = img_height
        
        if not exists(REPORT_PATH):
            os.mkdir(REPORT_PATH, mode=0o755)

        with open(f"{REPORT_PATH}/exb_report.txt") as file:
            raw_data = file.read().split()
            print(raw_data)
