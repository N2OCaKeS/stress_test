import os
from libs.libreport import ReportToConfluence
from libs.libpsb import perf
from libs.libtable import Report
from psb_conf import DEFAULT_SCALE_FACTOR, DEFAULT_TRANSACTIONS, DEFAULT_THREADS, \
    CLIENTS, CLIENTS_STEP, LIMITE_CLIENTS, REPORT_PATH, TEMPLATE_PATH, INFO_FILENAME, GRAPH_DESCRIPTIONS


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
                 test_cycle_version=None,
                 storage=False):
    
        self.username=username
        self.token=token
        self.c_space = conf_space
        self.c_pp = conf_parent_page
        self.c_np = conf_new_page_name
        self.grade_stand = grade_stand
        self.package=package
        self.tcv = test_cycle_version
        self.storage = storage

        if self.storage == 'nvme':
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
                    'storage':'NVME0n1 3.2Tb'},
                '4':{'grade':'MiddleServer(151)',
                    'cpu':'Intel(R) Xeon(R) CPU E5-2697 v3 @ 2.60GHz',
                    'ram':'256GB',
                    'storage':'NVME0n1 3.2Tb'}
            }
        else:
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


    def preset_publish(self, c_pp, c_np):

        confluence_report = ReportToConfluence(username=self.username, password=None, token=self.token)

        #создать flamegraph
        #print('# INFO # --- flamegraph')
        #perf()

        #создать страницу confluence
        confluence_report.create_confluence_page(self.c_space,
                                                 c_pp,
                                                 c_np)
        
        #прикрепить файлы к странице confluence
        for file in os.listdir(REPORT_PATH):
            confluence_report.attache_files('{}/{}'.format(REPORT_PATH, file),
                                            self.c_space,
                                            c_np)
            
        #генерация вступительной таблицы
        with open(INFO_FILENAME) as info:
            info_lst = info.read().split('\n')
        with open('{}/header_table_template.html'.format(TEMPLATE_PATH), 'r') as file:
            header_table_temp = file.read()
            header_table = header_table_temp.format(av=info_lst[0],
                                                    kernel=info_lst[1],
                                                    package_name=self.package,
                                                    package_vers=info_lst[2],
                                                    param_scale=str(DEFAULT_SCALE_FACTOR),
                                                    param_tr=str(DEFAULT_TRANSACTIONS),
                                                    param_th=str(DEFAULT_THREADS),
                                                    param_cl='{}-{}/{}'.format(CLIENTS, LIMITE_CLIENTS, CLIENTS_STEP),
                                                    arm_num=self.stands[self.grade_stand]['grade'],
                                                    arm_proc=self.stands[self.grade_stand]['cpu'],
                                                    arm_mem=self.stands[self.grade_stand]['ram'],
                                                    arm_st=self.stands[self.grade_stand]['storage'],
                                                    lead_time=info_lst[3])
            
        #создание страницы отчета
        rep = Report(report_file='{}/psb_report.txt'.format(REPORT_PATH))
        with open('{}/rating_template.html'.format(TEMPLATE_PATH), 'r') as template:
            rating_temp = template.read()
            rating = rating_temp.format(r=str(rep.get_total_rating()))

        with open('{}/psb_report_table.html'.format(REPORT_PATH), 'r') as file:
            main_table = file.read()

        with open('{}/img_template.html'.format(TEMPLATE_PATH), 'r') as template:
            images_lst = []
            img_temp = template.read()
            for file in os.listdir(REPORT_PATH):
                if file.endswith('png'):
                    images_lst.append(img_temp.format(page_id=confluence_report.get_confluence_page_id(self.c_space, c_np),
                                                     img_png=file,
                                                     description=GRAPH_DESCRIPTIONS[file]))
            images = '\n'.join(images_lst)

        html_page = '\n'.join([header_table, rating, main_table, images])

        #выкладываем информацию на страницу
        confluence_report.update_confluence_page(self.c_space, c_np, html_page)



    def run_publish(self):

        check_len_version = self.tcv.split('.')

        if len(check_len_version) == 4 and check_len_version[3] != 'UU':
            release_version = '.'.join(check_len_version[:3])
            rare_cpp = self.c_pp.split(' ')
            cpp = ' '.join([release_version if release_version in rare_cpp[i] else rare_cpp[i] for i in range(len(rare_cpp))])
            rare_cnp = self.c_np.split('_')
            cnp = '_'.join([release_version if release_version in rare_cnp[i] else rare_cnp[i] for i in range(len(rare_cnp))])

            self.preset_publish(self.c_pp, self.c_np)
            self.preset_publish(cpp, cnp)

        elif len(check_len_version) == 6 and check_len_version[3] == 'UU':
            release_version = '.'.join(check_len_version[:5])
            rare_cpp = self.c_pp.split(' ')
            cpp = ' '.join([release_version if release_version in rare_cpp[i] else rare_cpp[i] for i in range(len(rare_cpp))])
            rare_cnp = self.c_np.split('_')
            cnp = '_'.join([release_version if release_version in rare_cnp[i] else rare_cnp[i] for i in range(len(rare_cnp))])

            self.preset_publish(self.c_pp, self.c_np)
            self.preset_publish(cpp, cnp)

        else:
            self.preset_publish(self.c_pp, self.c_np)

            
