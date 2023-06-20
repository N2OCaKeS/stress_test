
import os
import re
from pathlib import Path
from libs.libreport import ReportToConfluence, ReportToJira
from sng_conf import INFO_FILENAME, TEMPLATE_PATH, GRAPH_DESCRIPTIONS, REPORT_PATH


class Public:
    '''
    Публикация результатов в confluence
    '''
    def __init__(self,
                 username=None,
                 token=None,
                 conf_space=None,
                 conf_parent_page=None,
                 conf_new_page_name=None,
                 grade_stand=None,
                 package=None,
                 file_system=None,
                 test_set=None):
    
        self.username=username
        self.token=token
        self.c_space = conf_space
        self.c_pp = conf_parent_page
        self.c_new_pn = conf_new_page_name
        self.grade_stand = grade_stand
        self.package=package
        self.fs=file_system
        self.ts=test_set
        self.stands = {
            '1':{'grade':'low(141)',
                 'cpu':'Intel(R) Core(TM) i7-11700 CPU @ 2.50GHz',
                 'ram':'32GB',
                 'storage':'Samsung NVME 970 EVO 2Тб'},
            '2':{'grade':'low(129)',
                 'cpu':'Intel(R) Core(TM) i5-8600K CPU @ 3.60GHz',
                 'ram':'32GB',
                 'storage':'SSD 512GB\sdb SSD 2TB'},
            '3':{'grade':'middle(151)',
                 'cpu':'Intel(R) Xeon(R) CPU E5-2697 v3 @ 2.60GHz',
                 'ram':'64GB',
                 'storage':'SSD Patriot Burst El 960GB'},
            '4':{'grade':'high(150)',
                 'cpu':'Intel(R) Xeon(R) Silver 4110 CPU @ 2.10GHz',
                 'ram':'128GB',
                 'storage':'SSD Patriot Burst Elite 960GB'}
        }

    def run_publish(self):

        confluence_report = ReportToConfluence(username=self.username, password=None, token=self.token)

        with open('{}/{}'.format(REPORT_PATH, 'sng_report.txt')) as report_txt:
            for line in report_txt:
                if "Load_time_execution" in line:
                    TIME_EXEC = line.split(" ")[1]
                if "Service_count" in line:
                    SERVICE_COUNT = line.split(" ")[1]
                if "Total_rating" in line:
                    TOTAL_RATING = line.split(" ")[1]

        #создать страницу confluence
        confluence_report.create_confluence_page(self.c_space,
                                                 self.c_pp,
                                                 self.c_new_pn)
        
        #прикрепить файлы к странице confluence
        for file in os.listdir(REPORT_PATH):
            confluence_report.attache_files(f'{REPORT_PATH}/{file}',
                                            self.c_space,
                                            self.c_new_pn)
            
        #генерация вступительной таблицы
        with open(INFO_FILENAME) as info:
            info_lst = info.read().split('\n')
        with open(f'{TEMPLATE_PATH}/header_table_template.html', 'r') as file:
            header_table_temp = file.read()
            header_table = header_table_temp.format(av=info_lst[0],
                                                    kernel=info_lst[1],
                                                    package_name='syslog-ng',
                                                    package_vers=info_lst[2],
                                                    param_time_exec=TIME_EXEC,
                                                    param_service_count=SERVICE_COUNT,
                                                    arm_num=self.stands[self.grade_stand]['grade'],
                                                    arm_proc=self.stands[self.grade_stand]['cpu'],
                                                    arm_mem=self.stands[self.grade_stand]['ram'],
                                                    arm_st=self.stands[self.grade_stand]['storage'],
                                                    lead_time=info_lst[3])       

        with open(f'{TEMPLATE_PATH}/rating_template.html', 'r') as template:
            rating_temp = template.read()
            rating = rating_temp.format(r=TOTAL_RATING)   

        with open(f'{TEMPLATE_PATH}/img_template.html', 'r') as template:
            images_lst = []
            img_temp = template.read()
            for file in os.listdir(REPORT_PATH):
                if file.endswith('png'):
                    images_lst.append(img_temp.format(page_id=confluence_report.get_confluence_page_id(self.c_space, self.c_new_pn), 
                                                    img_png=file,
                                                    description=GRAPH_DESCRIPTIONS[file]))
            images = '\n'.join(images_lst)

        html_page = '\n'.join([header_table, rating, images]) 

        #выкладываем информацию на страницу
        confluence_report.update_confluence_page(self.c_space, self.c_new_pn, html_page)
