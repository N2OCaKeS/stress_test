
from os import listdir
from re import search
from pathlib import Path
from libs.libreport import ReportToConfluence, ReportToJira
from lsb_conf import REPORT_DIR, TEMPLATE_DIR, INFO_DIR, \
    INFO_FILENAME, RATING_FILENAME, \
    GRAPH_DESCRIPTIONS, \
    STAND1_LOWER_LIMIT, STAND1_UPPER_LIMIT, STAND1_STEP, \
    STAND2_LOWER_LIMIT, STAND2_UPPER_LIMIT, STAND2_STEP, \
    STAND3_LOWER_LIMIT, STAND3_UPPER_LIMIT, STAND3_STEP, \
    STAND4_LOWER_LIMIT, STAND4_UPPER_LIMIT, STAND4_STEP

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

        if self.grade_stand == 1:
            self.limit = STAND1_UPPER_LIMIT
        elif self.grade_stand == 2:
            self.limit = STAND2_UPPER_LIMIT
        elif self.grade_stand == 3:
            self.limit = STAND3_UPPER_LIMIT
        elif self.grade_stand == 4:
            self.limit = STAND4_UPPER_LIMIT

    def run_publish(self):

        confluence_report = ReportToConfluence(username=self.username, password=None, token=self.token)

        #создать страницу confluence
        confluence_report.create_confluence_page(self.c_space,
                                                 self.c_pp,
                                                 self.c_new_pn)
        
        #прикрепить файлы к странице confluence
        for file in listdir(REPORT_DIR):
            confluence_report.attache_files(f'{REPORT_DIR}/{file}',
                                            self.c_space,
                                            self.c_new_pn)
            
        # генерация вступительной таблицы
        with open(f'{INFO_DIR}/{INFO_FILENAME}') as info:
            info_lst = info.read().split('\n')
        with open(f'{TEMPLATE_DIR}/header_table_template.html', 'r') as file:
            header_table_temp = file.read()
            header_table = header_table_temp.format(av=info_lst[0],
                                                    kernel=info_lst[1],
                                                    package_name='-',
                                                    package_vers=info_lst[2],
                                                    param_threads_quantity=f'{self.limit}',
                                                    arm_num=self.stands[self.grade_stand]['grade'],
                                                    arm_proc=self.stands[self.grade_stand]['cpu'],
                                                    arm_mem=self.stands[self.grade_stand]['ram'],
                                                    arm_st=self.stands[self.grade_stand]['storage'],
                                                    lead_time=info_lst[3])
            
        # создание страницы отчета
        with open(f'{TEMPLATE_DIR}/rating_template.html', 'r') as template:
            rating_temp = template.read()
        with open(f'{REPORT_DIR}/{RATING_FILENAME}', 'r') as report:
            report_temp = report.read()
            rating = rating_temp.format(dhry2reg_rating=search(r'dhry2reg: (-?\d+.\d+)', report_temp).group(1),
                                        whetstone_double_rating=search(r'whetstone-double: (-?\d+.\d+)', report_temp).group(1),
                                        execl_rating=search(r'execl: (-?\d+.\d+)', report_temp).group(1),
                                        fstime_rating=search(r'fstime: (-?\d+.\d+)', report_temp).group(1),
                                        fsbuffer_rating=search(r'fsbuffer: (-?\d+.\d+)', report_temp).group(1),
                                        fsdisk_rating=search(r'fsdisk: (-?\d+.\d+)', report_temp).group(1),
                                        pipe_rating=search(r'pipe: (-?\d+.\d+)', report_temp).group(1),
                                        context1_rating=search(r'context1: (-?\d+.\d+)', report_temp).group(1),
                                        spawn_rating=search(r'spawn: (-?\d+.\d+)', report_temp).group(1),
                                        shell1_rating=search(r'shell1: (-?\d+.\d+)', report_temp).group(1),
                                        shell8_rating=search(r'shell8: (-?\d+.\d+)', report_temp).group(1),
                                        syscall_rating=search(r'syscall: (-?\d+.\d+)', report_temp).group(1),
                                        total_rating=search(r'System Benchmarks Index Score\s+\d+', report_temp).group(1))
            
        tables = ''
        for file in Path(REPORT_DIR).glob('lsb_*_table.html'):
            with open(file, 'r') as f:
                tables + f.read() + '\n'

        # подготовка изображений
        with open('{}/img_template.html'.format(TEMPLATE_DIR), 'r') as template:
            images_lst = []
            img_temp = template.read()
            for file in listdir(REPORT_DIR):  # идем по списку
                if file.endswith('png'):  # если png
                    images_lst.append(img_temp.format(page_id=confluence_report.get_confluence_page_id(self.c_space, self.c_new_pn),
                                                    img_png=file,
                                                    description=GRAPH_DESCRIPTIONS[file]))  # добавляем в list
            images = '\n'.join(images_lst)

        html_page = '\n'.join([header_table, rating, tables, images])

        # выкладываем информацию на страницу
        confluence_report.update_confluence_page(self.c_space, self.c_new_pn, html_page)
