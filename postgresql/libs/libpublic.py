import os
import json
import pandas as pd
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
                 storage=False,
                 kernel_check=False,
                 balance=False):
    
        self.username = username
        self.token = token
        self.c_space = conf_space
        self.c_pp = conf_parent_page
        self.c_np = conf_new_page_name
        self.grade_stand = grade_stand
        self.package = package
        self.tcv = test_cycle_version
        self.storage = storage
        self.kernel_check = kernel_check
        self.balance = balance

        self.stands = {
                '1':{'grade':'low(141)',
                    'cpu':'Intel(R) Core(TM) i7-11700 CPU @ 2.50GHz',
                    'ram':'32GB',
                    'storage':'Samsung NVME 970 EVO 2Тб'},
                '2':{'grade':'low(129)',
                    'cpu':'Intel(R) Core(TM) i5-8600K CPU @ 3.60GHz',
                    'ram':'32GB',
                    'storage':'SSD 512GB\\sdb SSD 2TB'},
                '3':{'grade':'LowServer(150)',
                    'cpu':'Intel(R) Xeon(R) Silver 4110 CPU @ 2.10GHz',
                    'ram':'128GB',
                    'storage':'SAS SSD 3.8Tb'},
                '4':{'grade':'MiddleServer(151)',
                    'cpu':'Intel(R) Xeon(R) CPU E5-2697 v3 @ 2.60GHz',
                    'ram':'256GB',
                    'storage':'SAS SSD 3.8Tb'},
                '10':{'grade':'LowServer',
                    'cpu':'Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz',
                    'ram':'128GB',
                    'storage':'SAS SSD 3.8Tb'},
                '11':{'grade':'LowServer',
                    'cpu':'Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz',
                    'ram':'128GB',
                    'storage':'SAS SSD 3.8Tb'},
                '12':{'grade':'LowServer',
                    'cpu':'Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz',
                    'ram':'128GB',
                    'storage':'SAS SSD 3.8Tb'},
                '13':{'grade':'LowServer',
                    'cpu':'Intel(R) Xeon(R) Silver 4210 CPU @ 2.2GHz',
                    'ram':'128GB',
                    'storage':'SAS SSD 3.8Tb'},
        }

        if self.storage == 'nvme':
            self.stands['3']['storage'] = 'NVME0n1 3.2Tb'
            self.stands['4']['storage'] = 'NVME0n1 3.2Tb'   
            self.stands['10']['storage'] = 'NVME0n1 3.2Tb'  
            self.stands['11']['storage'] = 'NVME0n1 3.2Tb'
            self.stands['12']['storage'] = 'NVME0n1 3.2Tb'
            self.stands['13']['storage'] = 'NVME0n1 3.2Tb' 


    def preset_publish(self, c_pp, c_np, release_pp=False, release_np=False):

        confluence_report = ReportToConfluence(username=self.username, password=None, token=self.token)

        #создать flamegraph
        #print('# INFO # --- flamegraph')
        #perf()

        #создать страницу confluence
        def name_page(arg):
            top_page = f'STRESS ⬝ {str(arg).split("_")[1][:3]}'
            version_page = f'STRESS_report ⬝ {str(arg).split("_")[1]}'
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
            #прикрепить файлы к странице confluence
            for file in os.listdir(REPORT_PATH):
                confluence_report.attache_files('{}/{}'.format(REPORT_PATH, file),
                                                self.c_space,
                                                release_np)
                confluence_report.attache_files('{}/{}'.format(REPORT_PATH, file),
                                                self.c_space,
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
            for file in os.listdir(REPORT_PATH):
                confluence_report.attache_files('{}/{}'.format(REPORT_PATH, file),
                                                self.c_space,
                                                c_np)
            
        #генерация вступительной таблицы
        with open(INFO_FILENAME) as info:
            info_lst = info.read().split('\n')
        if self.kernel_check:
            with open('{}/header_table_template_kernel.html'.format(TEMPLATE_PATH), 'r') as file:
                header_table_temp = file.read()
                header_table = header_table_temp.format(av=info_lst[0],
                                                        kernel=info_lst[1],
                                                        package_name=self.package,
                                                        package_vers=info_lst[2],
                                                        arm_num=self.stands[self.grade_stand]['grade'],
                                                        arm_proc=self.stands[self.grade_stand]['cpu'],
                                                        arm_mem=self.stands[self.grade_stand]['ram'],
                                                        arm_st=self.stands[self.grade_stand]['storage'])
        elif self.balance:
            with open('{}/header_table_template_balance.html'.format(TEMPLATE_PATH), 'r') as file:
                header_table_temp = file.read()
                header_table = header_table_temp.format(av=info_lst[0],
                                                        kernel=info_lst[1],
                                                        package_name_psql=info_lst[4],
                                                        package_vers_psql=info_lst[2],
                                                        package_name_pgpool=info_lst[3],
                                                        package_vers_pgpool=info_lst[5],
                                                        arm_num=self.stands[self.grade_stand]['grade'],
                                                        arm_proc=self.stands[self.grade_stand]['cpu'],
                                                        arm_mem=self.stands[self.grade_stand]['ram'],
                                                        arm_st=self.stands[self.grade_stand]['storage'])
        else:
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
        if self.kernel_check:
            with open(f'{REPORT_PATH}/kernel_check.html', 'r') as file:
                kernel_table = file.read()
            head_row = '<p><h2 style="font-family: Century Gothic, sans-serif;"><b>Результаты:</b></h2></p>'
            html_page = '\n'.join([header_table, head_row, kernel_table])
        elif self.balance:
            with open(f'{REPORT_PATH}/results_balance.html', 'r') as file:
                balance_table = file.read()
            head_row = '<p><h2 style="font-family: Century Gothic, sans-serif;"><b>Результаты:</b></h2></p>'
            html_page = '\n'.join([header_table, head_row, balance_table])
        
        elif self.c_np.startswith('PSQL OLAP-hq'):
            head_row = '<p><h2 style="font-family: Century Gothic, sans-serif;"><b>Результаты:</b></h2></p>'
            
            psql_olap_hq_tables = dict()

            with open(f'{REPORT_PATH}/olap_results.json', 'r') as file:
                report_data = json.load(file)

            total_rating = report_data['total_rating']
            with open('{}/rating_template.html'.format(TEMPLATE_PATH), 'r') as template:
                rating_temp = template.read()
                rating = rating_temp.format(r=str(total_rating))

            sql_requests = report_data['result'].keys()
            for sql_request in sql_requests:
                print(f"SQL запрос: {sql_request}")
                data = {
                    "metric": ["p99", "p95", "p50", "min", "max"],
                    "value": [
                        report_data['result'][sql_request]['p99'],
                        report_data['result'][sql_request]['p95'],
                        report_data['result'][sql_request]['p50'],
                        report_data['result'][sql_request]['min'],
                        report_data['result'][sql_request]['max']
                    ]
                }
                df = pd.DataFrame(data)
                html_content = f"<h2>{sql_request}</h2>\n"
                html_content += df.to_html(index=False)
                psql_olap_hq_tables[sql_request]['table'] = html_content

            
            with open('{}/img_template.html'.format(TEMPLATE_PATH), 'r') as template:
                img_temp = template.read()
                for sql_request in sql_requests:
                    file = report_data['result'][sql_request]['speed_graph'].split('/')[-1]
                    if file.endswith('png'):
                        psql_olap_hq_tables[sql_request]['graph'] = img_temp.format(page_id=confluence_report.get_confluence_page_id(self.c_space, c_np),
                                                                                   img_png=file,
                                                                                   description=GRAPH_DESCRIPTIONS[file])
            html_psql_olap_hq_tables = []
            for sql_request in sql_requests:
                html_psql_olap_hq_tables.append(psql_olap_hq_tables[sql_request]['table'])
                html_psql_olap_hq_tables.append(psql_olap_hq_tables[sql_request]['graph'])
            
            olap_results = '\n'.join(html_psql_olap_hq_tables)

            html_page = '\n'.join([header_table, head_row, rating, olap_results])
        else:
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
                        psql_olap_hq_tables[sql_request]['graph']= img_temp.format(page_id=confluence_report.get_confluence_page_id(self.c_space, c_np), 
                                                                                   img_png=file, 
                                                                                   description=GRAPH_DESCRIPTIONS[file])
                images = '\n'.join(images_lst)

            html_page = '\n'.join([header_table, rating, main_table, images])

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

            
