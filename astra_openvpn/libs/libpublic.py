import os
import sys
from libs.libreport import ReportToConfluence
from libs.ovpnlib import info_list
sys.path.append(os.path.join(os.getcwd(), '..'))
from ovpn_conf import REPORT_PATH, TEMPLATE_PATH, INFO_FILENAME, RANGE, CONNECTIONS_PER_MINUTE, VMS_COUNT


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
                 balance=False,
                 testname="",
                 clients=RANGE,
                 vms_count=VMS_COUNT,
                 spawn_rate=CONNECTIONS_PER_MINUTE,
                 report_path=REPORT_PATH):
    
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
        self.testname = testname
        #------------INFO---------------
        self.clients = clients
        self.vms_count = vms_count
        self.spawn_rate = spawn_rate
        #-------------------------------
        self.report_path = report_path

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
                 'storage':'SAS SSD 3.8Tb'}
        }

        if self.storage == 'nvme':
            self.stands['3']['storage'] = 'NVME0n1 3.2Tb'
            self.stands['4']['storage'] = 'NVME0n1 3.2Tb'       


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
        
        info_list()
        #генерация вступительной таблицы
        with open(INFO_FILENAME) as info:
            info_lst = info.read().split('\n')

        # with open(f'{REPORT_PATH}/{REPORT_FILENAME}', 'r') as r:
        #     rps = r.read()

        # Делаем вступительную таблицу 
        print("DEBUG: grade_stand =", self.grade_stand)
        print("DEBUG: доступные ключи в self.stands:", self.stands.keys())
        with open(f'{TEMPLATE_PATH}/header_table_template_ovpn.html', 'r') as file:
            header_table_temp = file.read()
            header_table = header_table_temp.format(av=info_lst[0],
                                                    kernel=info_lst[1],
                                                    package=info_lst[2],                                                    
                                                    arm_num=self.stands[self.grade_stand]['grade'],
                                                    arm_proc=self.stands[self.grade_stand]['cpu'],
                                                    arm_mem=self.stands[self.grade_stand]['ram'],
                                                    arm_st=self.stands[self.grade_stand]['storage'],
                                                    total_clients=self.clients,
                                                    clients_nodes=self.vms_count-1, 
                                                    spawn_rate=self.spawn_rate*(self.vms_count-1))
        with open(f"{TEMPLATE_PATH}/test_report.html", "r", encoding="UTF-8") as file:
            test_table = file.read()


        # paths = [[f"{REPORT_PATH}/openvpn_testvm{i}" for i in self.vms_count-1],
        #          [f"{REPORT_PATH}/iperf_testvm{i}" for i in self.vms_count-1]]
        

        # r_docker = ""
        # r_server = ""
        # r_locust_proc = ""

        # for path in paths:
        #     with open(path, 'r') as f:
        #         lines = f.readlines()
        #         if len(lines) >= 2:
        #             header = lines[0].strip().rstrip(':')
        #             second_line = lines[1].strip()
        #             # Определяем тип файла по содержимому заголовка
        #             if "nginx_server" in header:
        #                 r_server = second_line
        #                 print("server:", r_server)
        #             elif "nginx_docker" in header:
        #                 r_docker = second_line
        #                 print("docker:", r_docker)
        #             elif "locust_proc" in header:
        #                 r_locust_proc = second_line
        #                 print("locust_proc:", r_locust_proc)

        # Делаем rating html
        # with open(f'{TEMPLATE_PATH}/rating_template.html', 'r') as template:
        #     rating_temp = template.read()
        #     rating = rating_temp.format(rd=r_docker, rl=r_locust_proc, rs=r_server)

        # # Таблицы на 3 теста
        # docker_stats = os.path.join(TEMPLATE_PATH, 'docker', 'results_stats.html')
        # docker_steps = os.path.join(TEMPLATE_PATH, 'docker', 'step_stats_summary.html')
        # locust_stats = os.path.join(TEMPLATE_PATH, 'locust', 'results_stats.html')
        # locust_steps = os.path.join(TEMPLATE_PATH, 'locust', 'step_stats_summary.html')
        # server_stats = os.path.join(TEMPLATE_PATH, 'server', 'results_stats.html')
        # server_steps = os.path.join(TEMPLATE_PATH, 'server', 'step_stats_summary.html')
        # with open(docker_stats, 'r') as f:
        #     docker_stats_html = f.read()
        # with open(docker_steps, 'r') as f:
        #     docker_steps_html = f.read()
        # with open(locust_stats, 'r') as f:
        #     locust_stats_html = f.read()
        # with open(locust_steps, 'r') as f:
        #     locust_steps_html = f.read()
        # with open(server_stats, 'r') as f:
        #     server_stats_html = f.read()
        # with open(server_steps, 'r') as f:
        #     server_steps_html = f.read()
        
        # 
        # html_page = '\n'.join([
        #     header_table,
        #     rating,
        #     "<hr></hr>"
        #     '<h2 style="font-family: Century Gothic, sans-serif; font-size: 16px; font-weight: bold; ">Детальные результаты по сценариям</h2>',
        #     '<h2 style="font-family: Century Gothic, sans-serif; font-size: 16px; font-weight: bold; ">1. Приложение в Docker | Нагрузчик в Docker 🐳</h2>', 
        #     "<h3>Общая статистика</h3>",
        #     docker_stats_html,
        #     docker_steps_html,

        #     '<h2 style="font-family: Century Gothic, sans-serif; font-size: 16px; font-weight: bold; ">2. Приложение в Docker | Нагрузчик на хосте 🐳</h2>',
        #     "<h3>Общая статистика</h3>",
        #     locust_stats_html,
        #     locust_steps_html,

        #     '<h2 style="font-family: Century Gothic, sans-serif; font-size: 16px; font-weight: bold; ">3. Приложение на хосте | Нагрузчик на хосте 💻</h2>',
        #     "<h3>Общая статистика</h3>",
        #     server_stats_html,
        #     server_steps_html])

        # создание страницы отчета
        html_page = '\n'.join([
            header_table,
            '<h2 style="font-family: Century Gothic, sans-serif; font-size: 16px; font-weight: bold; ">Результаты теста</h2>',
            test_table])

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
