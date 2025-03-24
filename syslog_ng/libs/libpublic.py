
import os
import re
from pathlib import Path
from libs.libreport import ReportToConfluence, ReportToJira
from sng_conf import INFO_FILENAME, TEMPLATE_PATH, GRAPH_DESCRIPTIONS, REPORT_PATH
from conf import VMCOUNT, VM_KERNEL, VM_INFONAME, VM_PACKAGE_VERS, STATUS_FILENAME, TABLE_STATUSES


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
        self.testname = self.c_np.split("_")[0]
        

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
            confluence_report.attache_files(f'{REPORT_PATH}/{file}',
                                            self.c_space,
                                            c_np)
        if self.testname == 'syslog-ng':
            with open('{}/{}'.format(REPORT_PATH, 'sng_report.txt')) as report_txt:
                for line in report_txt:
                    if "Load_time_execution" in line:
                        TIME_EXEC = line.split(" ")[1]
                    if "Service_count" in line:
                        SERVICE_COUNT = line.split(" ")[1]
                    if "Total_rating" in line:
                        TOTAL_RATING = line.split(" ")[1]
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
                        images_lst.append(img_temp.format(page_id=confluence_report.get_confluence_page_id(self.c_space, c_np), 
                                                        img_png=file,
                                                        description=GRAPH_DESCRIPTIONS[file]))
                images = '\n'.join(images_lst)

            html_page = '\n'.join([header_table, rating, images])

        elif self.testname == 'syslog-ng cwl':
            """
                TODO INFO_FILENAME ЗДЕСЬ ПОКА ПОД ВОПРОСОМ !!!!
            """
            with open(INFO_FILENAME) as info:
                info_lst = info.read().split('\n')

            with open(f'{REPORT_PATH}/{VM_INFONAME}') as info:
                vm_info = info.read()

            with open(f'{REPORT_PATH}/{VM_KERNEL}') as info:
                vm_kernel = info.read()

            with open(f'{REPORT_PATH}/{VM_PACKAGE_VERS}') as info:
                package_version = info.read()

            with open(f'{TEMPLATE_PATH}/header_table_template_cwl.html', 'r') as file:
                header_table_temp = file.read()
                header_table = header_table_temp.format(av=info_lst[0],
                                                        kernel=info_lst[1],
                                                        vm_av=vm_info,
                                                        vm_kernel=vm_kernel,
                                                        vm_count=VMCOUNT,
                                                        package_name='syslog-ng',
                                                        package_vers=package_version,
                                                        arm_num=self.stands[self.grade_stand]['grade'],
                                                        arm_proc=self.stands[self.grade_stand]['cpu'],
                                                        arm_mem=self.stands[self.grade_stand]['ram'],
                                                        arm_st=self.stands[self.grade_stand]['storage'],
                                                        lead_time=info_lst[3]
                                                        )
            
            with open(f'{REPORT_PATH}/{STATUS_FILENAME}') as status_file:
                itog_status = status_file.read()
            
            if os.path.isfile(f'{REPORT_PATH}/{TABLE_STATUSES}'):
                with open(f'{REPORT_PATH}/{TABLE_STATUSES}', 'r') as table_statuses_html:
                    table_statuses = table_statuses_html.read()
            else:
                table_statuses = ""
            
            html_page = '\n'.join([header_table, table_statuses, itog_status])


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

            