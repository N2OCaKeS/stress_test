
import os
import re
from pathlib import Path
from libs.libreport import ReportToConfluence, ReportToJira
from libs.libaub import astra_version
from aub_conf import REPORT_DIR, TEMPLATE_DIR, INFO_FILENAME, GRAPH_DESCRIPTIONS, \
    PS_LOWER_LIMIT, PS_UPPER_LIMIT, PS_STEP, DEFAULT_PS_LIFETIME, DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY


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
                 test_set=None,
                 test_cycle_version=None):
    
        self.username=username
        self.token=token
        self.c_space = conf_space
        self.c_pp = conf_parent_page
        self.c_np = conf_new_page_name
        self.grade_stand = grade_stand
        self.package=package
        self.fs=file_system
        self.ts=test_set
        self.tcv = test_cycle_version

        self.stands = {
            '1':{'grade':'low(141)',
                 'cpu':'Intel(R) Core(TM) i7-11700 CPU @ 2.50GHz',
                 'ram':'32GB',
                 'storage':'Samsung NVME 970 EVO 2Тб'},
            '2':{'grade':'low(129)',
                 'cpu':'Intel(R) Core(TM) i5-8600K CPU @ 3.60GHz',
                 'ram':'32GB',
                 'storage':'SSD 512GB\sdb SSD 2TB'},
            '3':{'grade':'LowServer(150)',
                 'cpu':'Intel(R) Xeon(R) Silver 4110 CPU @ 2.10GHz',
                 'ram':'128GB',
                 'storage':'SAS SSD 3.8Tb'},
            '4':{'grade':'MiddleServer(151)',
                 'cpu':'Intel(R) Xeon(R) CPU E5-2697 v3 @ 2.60GHz',
                 'ram':'256GB',
                 'storage':'SAS SSD 3.8Tb'}
        }

    def preset_publish(self, c_pp, c_np, release_pp=False, release_np=False):

        confluence_report = ReportToConfluence(username=self.username, password=None, token=self.token)

        #создать страницу confluence
        def name_page(arg):
            top_page = f'STRESS ⬝ {str(arg).split("_")[1][:3]}'
            version_page = f'STRESS ⬝ {str(arg).split("_")[1]}'
            return top_page, version_page

        if release_pp and release_np:
            confluence_report.create_confluence_page(self.c_space,
                                                     name_page(release_np)[0],
                                                     name_page(release_np)[1])
            confluence_report.create_confluence_page(self.c_space,
                                                     name_page(release_np)[1],
                                                     release_pp)
            confluence_report.create_confluence_page(self.c_space,
                                                     release_pp,
                                                     release_np)
            confluence_report.create_confluence_page(self.c_space,
                                                     name_page(release_np)[1],
                                                     name_page(c_np)[1])
            confluence_report.create_confluence_page(self.c_space,
                                                     name_page(c_np)[1],
                                                     c_pp)
            confluence_report.create_confluence_page(self.c_space,
                                                     c_pp,
                                                     c_np)
        else:
            confluence_report.create_confluence_page(self.c_space,
                                                     name_page(c_np)[0],
                                                     name_page(c_np)[1])
            confluence_report.create_confluence_page(self.c_space,
                                                     name_page(c_np)[1],
                                                     c_pp)
            confluence_report.create_confluence_page(self.c_space,
                                                     c_pp,
                                                     c_np)
        
        #прикрепить файлы к странице confluence
        for file in os.listdir(REPORT_DIR):
            confluence_report.attache_files(f'{REPORT_DIR}/{file}',
                                            self.c_space,
                                            c_np)
            
        #генерация вступительной таблицы
        with open(INFO_FILENAME) as info:
            info_lst = info.read().split('\n')
        with open(f'{TEMPLATE_DIR}/header_table_template.html', 'r') as file:
            header_table_temp = file.read()
            header_table = header_table_temp.format(av=info_lst[0],
                                                    kernel=info_lst[1],
                                                    package_name='auditd',
                                                    package_vers=info_lst[2],
                                                    param_ps_lifetime=DEFAULT_PS_LIFETIME,
                                                    param_ps_delay=DEFAULT_PS_EVENT_RE_INITIALIZATION_DELAY,
                                                    param_ps_quantity='{}-{}/{}'.format(PS_LOWER_LIMIT,
                                                                                        PS_UPPER_LIMIT,
                                                                                        PS_STEP),
                                                    arm_num=self.stands[self.grade_stand]['grade'],
                                                    arm_proc=self.stands[self.grade_stand]['cpu'],
                                                    arm_mem=self.stands[self.grade_stand]['ram'],
                                                    arm_st=self.stands[self.grade_stand]['storage'],
                                                    lead_time=info_lst[3])

        #создание страницы отчета
        with open(f'{TEMPLATE_DIR}/rating_template.html', 'r') as template:
            rating_temp = template.read()
        with open(f'{REPORT_DIR}/aub_report.txt', 'r') as report:
            report_temp = report.read()
            rating = rating_temp.format(type=self.ts,
                                        total_latency_rating=re.search(r'total latency rating: (-?\d+.\d+)', report_temp).group(1),
                                        total_losses_rating=re.search(r'total losses rating: (-?\d+.\d+)', report_temp).group(1),
                                        total_auditd_rating=re.search(r'total auditd rating: (-?\d+.\d+)', report_temp).group(1))

        tables = ''
        for file in Path(REPORT_DIR).glob('aub_*_table.html'):
            with open(file, 'r') as f:
                tables + f.read() + '\n'

        # подготовка изображений
        with open(f'{TEMPLATE_DIR}/img_template.html', 'r') as template:
            images_lst = []
            img_temp = template.read()
            for file in os.listdir(REPORT_DIR):
                if file.endswith('png'):
                    images_lst.append(img_temp.format(page_id=confluence_report.get_confluence_page_id(self.c_space, c_np),
                                                    img_png=file,
                                                    description=GRAPH_DESCRIPTIONS[file]))
            images = '\n'.join(images_lst)

        html_page = '\n'.join([header_table, rating, tables, images])

        #выкладываем информацию на страницу
        if release_pp and release_np:
            confluence_report.update_confluence_page(self.c_space, c_np, html_page)
            confluence_report.update_confluence_page(self.c_space, release_np, html_page)
        else:
            confluence_report.update_confluence_page(self.c_space, c_np, html_page)  


    def run_publish(self):

        check_len_version = self.tcv.split('.')

        if len(check_len_version) == 4 and check_len_version[3] != 'UU':
            release_version = '.'.join(check_len_version[:3])
            rare_cpp = self.c_pp.split(' ')
            cpp = ' '.join([release_version if release_version in rare_cpp[i] else rare_cpp[i] for i in range(len(rare_cpp))])
            rare_cnp = self.c_np.split('_')
            cnp = '_'.join([release_version if release_version in rare_cnp[i] else rare_cnp[i] for i in range(len(rare_cnp))])

            self.preset_publish(self.c_pp, self.c_np, release_pp=cpp, release_np=cnp)

        elif len(check_len_version) == 6 and check_len_version[3] == 'UU':
            release_version = '.'.join(check_len_version[:5])
            rare_cpp = self.c_pp.split(' ')
            cpp = ' '.join([release_version if release_version in rare_cpp[i] else rare_cpp[i] for i in range(len(rare_cpp))])
            rare_cnp = self.c_np.split('_')
            cnp = '_'.join([release_version if release_version in rare_cnp[i] else rare_cnp[i] for i in range(len(rare_cnp))])

            self.preset_publish(self.c_pp, self.c_np, release_pp=cpp, release_np=cnp)

        else:
            self.preset_publish(self.c_pp, self.c_np)

            
