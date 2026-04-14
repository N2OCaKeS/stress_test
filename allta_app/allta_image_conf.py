import json
import requests

from os import getenv
from dotenv import load_dotenv



def get_allta_conf():
    with open('./allta_conf.json', 'r') as r:
        return json.load(r)

#################################################################################################################################################
#Venv
#################################################################################################################################################
VENV_PATH = '/home/u/python/Python-3.12.1/venv/bin/python3.12'



#################################################################################################################################################
#ALLTA network
#################################################################################################################################################
allta_network = {
    'ip':'10.177.103.10',
    'url':'http://allta.devos.astralinux.ru',
    'fqdn':'allta.devos.astralinux.ru'
}



#################################################################################################################################################
#Перечень IP используемых серверов
#################################################################################################################################################
stands_ip = {
    'stand1':'10.177.103.201',
    'stand2':'10.177.103.202',
    'stand3':'10.177.103.204',
    'stand4':'10.177.103.203',
    'stand5':'10.177.103.205',
    'stand6':'10.177.103.101',
    'stand7':'10.177.103.102',
    'stand8':'10.177.103.103',
    'stand9':'10.177.103.104',
    'stand10':'10.177.103.206',
    'stand11':'10.177.103.207',
    'stand12':'10.177.103.208',
    'stand13':'10.177.103.209'
}



#################################################################################################################################################
#Перечень типов и соотношений используемых стендов
#################################################################################################################################################
stands_type = {
    'virt':{
        'stand1':'VM Test WorkStation',
        'stand2':'VM Test WorkStation',
        'stand6':'VM TestStation',
        'stand7':'VM TestStation',
        'stand8':'VM TestStation',
        'stand9':'VM TestStation'
    },
    'phys':{
        'stand3':'LowServer',
        'stand4':'MiddleServer',
        'stand5':'HighServer',
        'stand10':'LowServer2',
        'stand11':'LowServer3',
        'stand12':'LowServer4',
        'stand13':'LowServer5'
    }
}


#################################################################################################################################################
#Atlassian & Astra URLs
#################################################################################################################################################
JIRA_URL = 'jira.astralinux.ru'
CONFLUENCE_URL = 'life.astralinux.ru'
GIT_URL = 'git.astralinux.ru'
RELEASES_URL = 'releases.devos.astralinux.ru'



#################################################################################################################################################
#Astra DNS
#################################################################################################################################################
ASTRA_DNS = ['10.177.128.198', '10.177.180.246', '10.177.181.142']



#################################################################################################################################################
#Режимы защищенности
#################################################################################################################################################
modes = {
    'orel':'0',
    'voronezh':'1',
    'smolensk':'2',
}



#################################################################################################################################################
#Симлинки на страницы с местами хранения результатов
#################################################################################################################################################
tests_list = {'PostgreSQL':     ['postgresql', 'psql parsec', 'psql kernels', 'psql vanilla', 'psql balance', 'PSQL OLAP-hq',
                                 'postgresql-sm', 'postgresql-aud-off', 'tantor vanilla', 'tantor kernels', 'psql oom'],
            'Файловые системы': ['XFS', 'EXT2', 'EXT3', 'EXT4', 'EXT4 parsec', 'NTFS', 'XFS parsec', 'FAT', 'EXFAT', 'OCFS2', 'CEPH', 'CEPH fio', 'CEPH parsec'],
            'Системные службы': ['auditd-p', 'auditd-f', 'auditd-u', 'syslog-ng', 'RAM-overflow', 'SD-overflow', 'syslog-ng-cwl', 'AOpenVPNcc',
                                 'Dovecot-IMAP', 'Exim4-SMTP', 'SegFault', "XFS mem leak"],
            'UnixBench':        ['unix', 'unix parsec'],
            'FreeIPA':          ['FreeIPA auth', 'FreeIPA c-users', "FreeIPA plugin"],
            'Parsec':           ['parsec impact-fs', 'parsec impact-fs aud-off', 'digsig-cdt'],
            'Apache':           ['apache-rp'],
            'Docker/Podman/LXC':['docker-wa'],
            'Qemu/KVM/Libvirt': ['steal time', 'steal time-sm', 'FIO', 'vUnixBench', 'vPingPong', 'FIO large'],
            'Network':          ['InitOnFree']}

def parent_page_list():
    parent_page_list = {
        key:{value:f'STRESS_report {key} ⬝ {topic}' for topic in tests_list for value in tests_list[topic]}  
                        for key in get_allta_conf()['release_version']
                        }
    return parent_page_list


# # # Example:
# {'1.8.0.1': {'postgresql': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'psql parsec': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'psql kernels': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'psql vanilla': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'postgresql-sm': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'postgresql-aud-off': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'tantor vanilla': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'tantor kernels': 'STRESS 1.8.0.1 ⬝ PostgreSQL', 
#              'XFS': 'STRESS 1.8.0.1 ⬝ Файловые системы', 
#              'EXT4': 'STRESS 1.8.0.1 ⬝ Файловые системы', 
#              'EXT4 parsec': 'STRESS 1.8.0.1 ⬝ Файловые системы', 
#              'NTFS': 'STRESS 1.8.0.1 ⬝ Файловые системы', 
#              'XFS parsec': 'STRESS 1.8.0.1 ⬝ Файловые системы', 
#              'auditd-p': 'STRESS 1.8.0.1 ⬝ Системные службы', 
#              'auditd-f': 'STRESS 1.8.0.1 ⬝ Системные службы', 
#              'auditd-u': 'STRESS 1.8.0.1 ⬝ Системные службы', 
#              'syslog-ng': 'STRESS 1.8.0.1 ⬝ Системные службы', 
#              'RAM-overflow': 'STRESS 1.8.0.1 ⬝ Системные службы', 
#              'SD-overflow': 'STRESS 1.8.0.1 ⬝ Системные службы', 
#              'unix': 'STRESS 1.8.0.1 ⬝ UnixBench', 
#              'unix parsec': 'STRESS 1.8.0.1 ⬝ UnixBench'}}



#################################################################################################################################################
#Ветки проектов в git
#################################################################################################################################################
branches = {
    'freeipa authentication test':'freeipa',
    'freeipa create users test':'freeipa',
    'freeipa plugin test':'freeipa',
    'file system benchmark. EXT4':'file_systems',
    'file system benchmark. XFS':'file_systems',
    'postgresql benchmark':'postgresql',
    'postgresql benchmark balance':'postgresql',
    'postgresql benchmark oom':'postgresql',
    'postgresql benchmark kernels':'postgresql',
    'postgresql benchmark parsec':'postgresql',
    'postgresql benchmark vanilla':'postgresql',
    'postgresql benchmark smol':'postgresql',
    'postgresql benchmark audit-off':'postgresql',
    'tantor benchmark vanilla':'postgresql',
    'tantor benchmark kernels':'postgresql',
    'ceph benchmark':'cluster_file_systems',
    'ceph fio benchmark':'cluster_file_systems',
    'ceph parsec benchmark':'cluster_file_systems',
    'file system benchmark. OCFS2':'cluster_file_systems',
    'file system benchmark. NTFS':'file_systems',
    'file system benchmark. EXT3':'file_systems',
    'file system benchmark. EXT2':'file_systems',
    'file system benchmark. FAT':'file_systems',
    'file system benchmark. EXFAT':'file_systems',
    'file system benchmark. EXT4 parsec':'file_systems',
    'file system benchmark. OCFS2 parsec':'cluster_file_systems',
    'file system benchmark. XFS parsec':'file_systems',
    'auditd benchmark. psaud':'auditd',
    'auditd benchmark. fileaud':'auditd',
    'auditd benchmark. useraud':'auditd',
    'syslog-ng benchmark':'syslog_ng',
    'syslog-ng benchmark check-write-log':'syslog_ng',
    'linux_system_benchmark. UnixBench':'linux_system',
    'linux_system_benchmark. UnixBench parsec':'linux_system',
    'ram overflow':'overflow',
    'storage drive overflow':'overflow',
    'Parsec impact fs benchmark':'parsec',
    'Parsec impact fs benchmark audit-off':'parsec',
    'DIGSIG. Check digsig time':'parsec',
    'Apache_ReverseProxy':'apache2',
    'Steal time':'virt',
    'Steal time smolensk':'virt',
    'FIO benchmark':'virt',
    'Large FIO benchmark':'virt',
    'Virt UnixBench':'virt',
    'vPingPong':'virt',
    'docker web-application':'docker',
    'astra openvpn client connections':'astra_openvpn',
    'dovecot benchmark':'exim',
    'exim benchmark':'exim',
    'Network benchmark. Init_on_free':'network',
    'segmentation_fault':'kernel',
    'postgresql benchmark olap':'postgresql',
    'XFS. Memory leak':'kernel'
}



#################################################################################################################################################
#Tree_ID страницы тестового прогона
#################################################################################################################################################
def cycle_tree_index():
    return get_allta_conf()['cycle_tree_index']



#################################################################################################################################################
#Конвертируемый перечень тестов
#################################################################################################################################################
tests = {
    'Parsec impact fs benchmark audit-off':'parsec impact-fs aud-off',
    'Parsec impact fs benchmark':'parsec impact-fs',
    'DIGSIG. Check digsig time': 'digsig-cdt',
    'freeipa authentication test':'FreeIPA auth',
    'freeipa create users test':'FreeIPA c-users',
    'freeipa plugin test':'FreeIPA plugin',
    'file system benchmark. EXT4':'EXT4',
    'file system benchmark. XFS':'XFS',
    'postgresql benchmark':'postgresql',
    'postgresql benchmark balance':'psql balance',
    'postgresql benchmark oom':'psql oom',
    'postgresql benchmark kernels':'psql kernels',
    'postgresql benchmark parsec':'psql parsec',
    'postgresql benchmark vanilla':'psql vanilla',
    'postgresql benchmark smol':'postgresql-sm',
    'postgresql benchmark audit-off':'postgresql-aud-off',
    'tantor benchmark vanilla':'tantor vanilla',
    'tantor benchmark kernels':'tantor kernels',
    'ceph benchmark':'CEPH',
    'ceph fio benchmark':'CEPH fio',
    'ceph parsec benchmark':'CEPH parsec',
    'file system benchmark. OCFS2':'OCFS2',
    'file system benchmark. NTFS':'NTFS',
    'file system benchmark. EXT3':'EXT3',
    'file system benchmark. EXT2':'EXT2',
    'file system benchmark. FAT':'FAT',
    'file system benchmark. EXFAT':'EXFAT',
    'file system benchmark. EXT4 parsec':'EXT4 parsec',
    'file system benchmark. XFS parsec':'XFS parsec',
    'file system benchmark. OCFS2 parsec':'OCFS2 parsec',
    'auditd benchmark. psaud':'auditd-p',
    'auditd benchmark. fileaud':'auditd-f',
    'auditd benchmark. useraud':'auditd-u',
    'syslog-ng benchmark':'syslog-ng',
    'syslog-ng benchmark check-write-log':'syslog-ng-cwl',
    'linux_system_benchmark. UnixBench':'unix',
    'linux_system_benchmark. UnixBench parsec':'unix parsec',
    'storage drive overflow':'SD-overflow',
    'ram overflow':'RAM-overflow',
    'Apache_ReverseProxy':'apache-rp',
    'Steal time':'steal time',
    'Steal time smolensk':'steal time-sm',
    'FIO benchmark':'FIO',
    'Large FIO benchmark':'FIO large',
    'Virt UnixBench':'vUnixBench',
    'vPingPong':'vPingPong',
    'docker web-application':'docker-wa',
    'astra openvpn client connections':'AOpenVPNcc',
    'dovecot benchmark':'Dovecot-IMAP',
    'exim benchmark':'Exim4-SMTP',
    'Network benchmark. Init_on_free':'InitOnFree',
    'segmentation_fault':'SegFault',
    'postgresql benchmark olap':'PSQL OLAP-hq',
    'XFS. Memory leak': 'XFS mem leak'
}



#################################################################################################################################################
#Настройки для внутренней СУБД
#################################################################################################################################################
psyc = {
        'host':'127.0.0.1',
        'database':'bendiks',
        'user':'postgres',
        'password':'1'
}    

psyc_conf = {
            'host':'127.0.0.1',
            'database':'b_config',
            'user':'postgres',
            'password':'1'
} 



#################################################################################################################################################
#Основной перечень тестов
#################################################################################################################################################
group_tests = ['_stand3 group', '_stand4 group', '_stand10 group', '_stand11 group', '_stand12 group', '_stand13 group']
main_tests = ['XFS', 'EXT4', 'NTFS', 'EXT4 parsec', 'postgresql', 'postgresql-sm', 'psql parsec', 'auditd-p', 'auditd-u', 'tantor vanilla',
              'auditd-f', 'syslog-ng', 'unix', 'postgresql-aud-off', 'SD-overflow', 'RAM-overflow', 'XFS parsec', 'psql vanilla', 'syslog-ng-cwl',
              'psql kernels', 'tantor kernels', 'unix parsec', 'psql balance', 'FreeIPA auth', 'parsec impact-fs', 'parsec impact-fs aud-off',
              'apache-rp', 'steal time', 'EXT2', 'EXT3', 'FAT', 'EXFAT', 'FIO', 'vUnixBench', 'vPingPong', 'OCFS2', 'steal time-sm', 'psql oom',
              'digsig-cdt', 'docker-wa', 'CEPH', 'CEPH fio', 'FreeIPA c-users', 'CEPH parsec', 'AOpenVPNcc', 'Dovecot-IMAP', 'Exim4-SMTP',
              'FIO large', 'InitOnFree', 'SegFault', 'PSQL OLAP-hq', 'FreeIPA plugin', 'XFS mem leak']



#################################################################################################################################################
#Перечень тестов БРЕСТ
#################################################################################################################################################
brest_tests = ['apache-graph']



#################################################################################################################################################
#Доступные релизы (следует указывать при наличии снимка в Clonezilla)
#################################################################################################################################################
def releases():
    return get_allta_conf()['releases']



#################################################################################################################################################
#Перечень доступных ядер
#################################################################################################################################################
def kernels():
    return get_allta_conf()['kernels']



#################################################################################################################################################
#Перечень ВМ, используемых в качестве стендов
#################################################################################################################################################
test_station_vms = {
        'stand1': 'work-station1',
        'stand2': 'work-station2',
        'stand6': 'virtual-station1',
        'stand7': 'virtual-station2',
        'stand8': 'virtual-station3',
        'stand9': 'virtual-station4'
    }



#################################################################################################################################################
#Перечень стендов, отображаемых на разных страницах
#################################################################################################################################################
main_stands = ['stand1', 'stand2', 'stand3', 'stand4', 'stand5', 'stand6', 'stand7', 'stand8', 'stand9',
               'stand10', 'stand11', 'stand12', 'stand13']
mobile_stands = ['stand1', 'stand2', 'stand3', 'stand4', 'stand5']
brest_stands = []




#################################################################################################################################################
#Перечень настроек, используемых для создания тестовых прогонов
#################################################################################################################################################
def repo_path():
    return get_allta_conf()['repo_path']

def rc_list():
    return get_allta_conf()['rc_list']

def releases_list():
    return get_allta_conf()['releases_list']

def stp_version():
    return sorted(list(set(rc_list() + releases_list())))

def changelog_testcycle_handler(rc: str, final=False) -> tuple:
    def get_topic() -> list:
        request = f'http://10.177.103.10:8989/get_components_for_testrun_by_changelog?astra_linux_build_version={rc}&first_level_dependencies=true&return_dct_component_with_packages=false'
        response = requests.get(request).json()
        print(response)
        print(response['result'])
        if response['status'] != 'success':
            return []
        else: return response['result']


    def handler(topic: list) -> tuple:
        """
        Перечень тестов, разделенных по уровням защищенности и стендам
        """
        topics = {
            'orel_stand3':      ['EXT2', 'EXT3', 'EXT4', 'FAT',  'EXFAT', 'XFS', 'FreeIPA auth', 'unix', 'FreeIPA c-users'],
            'smolensk_stand3':  ['EXT4 parsec', 'XFS parsec', 'unix parsec', 'FreeIPA plugin'],
            'orel_stand4':      ['postgresql-aud-off', 'postgresql', 'psql balance', 'steal time', 'psql kernels'],
            'smolensk_stand4':  ['postgresql-sm', 'psql parsec', 'psql vanilla', 'steal time-sm'],
            'orel_stand10':     ['NTFS', 'OCFS2', 'CEPH', 'CEPH fio'],
            'smolensk_stand10': ['CEPH parsec'],
            'orel_stand11':     ['docker-wa', 'FIO', 'vUnixBench', 'vPingPong', 'AOpenVPNcc', 'Dovecot-IMAP', 'Exim4-SMTP', 'FIO large'],
            'smolensk_stand11': ['parsec impact-fs', 'parsec impact-fs aud-off', 'psql oom'],
            'orel_stand12':     ['syslog-ng', 'InitOnFree', 'SegFault', 'XFS mem leak'],
            'smolensk_stand12': ['auditd-f', 'auditd-p', 'auditd-u', 'digsig-cdt', 'apache-rp', 'PSQL OLAP-hq'],
            'orel_stand13':     ['syslog-ng-cwl'],
            'smolensk_stand13': []
        }


        if str(rc).endswith('.1') and not 'UU' in str(rc) or final:
            return tuple(topics.values())
        else:
            return tuple([test for top in topic for test in tests_list[top] if test in topics[i]] for i in topics.keys())

    return handler(topic=get_topic())

stands_groups = {
    'stand3_group': ['EXT2', 'EXT3', 'EXT4', 'FAT',  'EXFAT', 'XFS', 'EXT4 parsec', 'XFS parsec', 'FreeIPA auth', 'unix', 'unix parsec', 'FreeIPA c-users', 'FreeIPA plugin'],
    'stand4_group': ['postgresql-aud-off', 'postgresql', 'postgresql-sm', 'psql parsec', 'psql vanilla', 'psql balance', 'steal time', 'steal time-sm', 'psql kernels'],
    'stand10_group':['NTFS', 'OCFS2', 'CEPH', 'CEPH fio', 'CEPH parsec'],

    'stand11_group':['parsec impact-fs', 'parsec impact-fs aud-off', 'docker-wa', 'FIO', 'vUnixBench', 'vPingPong', 'psql oom', 'AOpenVPNcc', 'Dovecot-IMAP', 'Exim4-SMTP', 'FIO large'],
    'stand12_group':['syslog-ng', 'auditd-f', 'auditd-p', 'auditd-u', 'digsig-cdt', 'apache-rp', 'InitOnFree', 'SegFault', 'PSQL OLAP-hq', 'XFS mem leak'],

    'stand13_group':['syslog-ng-cwl']
}

test_run_stands = [f'stand{x}' for x in ['3', '4', '10', '11', '12', '13']] #range(3, 6, 1)]
test_run_modes = ['orel', 'smolensk']
tests_case_zefir_key = {
    'postgresql':'BT-T7555',
    'psql kernels':'BT-T13230',
    'postgresql-aud-off':'BT-T9169',
    'postgresql-sm':'BT-T9106',
    'psql parsec':'BT-T9638',
    'psql vanilla':'BT-T9673',
    'tantor vanilla':'BT-T9695',
    'tantor kernels':'BT-T13243',
    'EXT4':'BT-T7562',
    'EXT4 parsec':'BT-T8822',
    'NTFS':'BT-T7564',
    'XFS':'BT-T8069',
    'XFS parsec':'BT-T9488',
    'unix':'BT-T8737',
    'unix parsec':'BT-T13267',
    'RAM-overflow':'BT-T9170',
    'SD-overflow':'BT-T9172',
    'auditd-f':'BT-T8215',
    'auditd-p':'BT-T8213',
    'auditd-u':'BT-T8214',
    'syslog-ng':'BT-T8119',
    'syslog-ng-cwl':'BT-T16383',
    'psql balance':'BT-T13362',
    'FreeIPA auth':'BT-T13481',
    'parsec impact-fs':'BT-T13486',
    'parsec impact-fs aud-off':'BT-T13489',
    'apache-rp':'BT-T13621',
    'steal time':'BT-T13735',
    'EXT2':'BT-T7560',
    'EXT3':'BT-T7561',
    'FAT':'BT-T7563',
    'EXFAT':'BT-T13736',
    'FIO':'BT-T13864',
    'vUnixBench':'BT-T14097',
    'vPingPong':'BT-T14145',
    'OCFS2':'BT-T7848',
    'steal time-sm':'BT-T15186',
    'psql oom':'BT-T16134',
    'digsig-cdt':'BT-T16391',
    'docker-wa':'BT-T16564',
    'CEPH':'BT-T17640', 
    'CEPH fio':'BT-T17641',
    'FreeIPA c-users':'BT-T17856',
    'CEPH parsec':'BT-T18198',
    'AOpenVPNcc':'BT-T18201',
    'Dovecot-IMAP':'BT-T18555',
    'Exim4-SMTP':'BT-T18554',
    'FIO large':'BT-T18278',
    'InitOnFree':'BT-T18909',
    'SegFault':'BT-T18927',
    'PSQL OLAP-hq':'BT-T19100',
    'FreeIPA plugin':'BT-T19492',
    'XFS mem leak':'BT-T19753',
}



#################################################################################################################################################
#Перечень колонок zefir для СТП
#################################################################################################################################################
testname_columns = {
                    'file system benchmark. EXT4':'FS_EXT4', 'file system benchmark. XFS':'FS_XFS', 
                    'file system benchmark. OCFS2':'FS_OCFS2', 'file system benchmark. NTFS':'FS_NTFS',
                    'auditd benchmark. psaud':'Auditd_psaud', 'linux_system_benchmark. UnixBench':'UnixBench',
                    'file system benchmark. EXT3':'FS_EXT3', 'file system benchmark. EXT2':'FS_EXT2',
                    'file system benchmark. FAT':'FS_FAT', 'syslog-ng benchmark':'Syslog-NG', 'postgresql benchmark':'PostgreSQL',
                    'file system benchmark. EXT4 parsec':'FS_EXT4_parsec', 'auditd benchmark. fileaud':'Auditd_fileaud',
                    'auditd benchmark. useraud':'Auditd_useraud', 'file system benchmark. OCFS2 parsec':'FS_OCFS2_parsec',
                    'postgresql benchmark smol':'PostgreSQL_smol', 'postgresql benchmark audit-off':'PSQL_audit-off',
                    'storage drive overflow':'SD_overflow', 'ram overflow':'RAM_overflow', 'file system benchmark. XFS parsec':'FS_XFS_parsec',
                    'postgresql benchmark parsec':'PSQL_parsec', 'postgresql benchmark vanilla':'PSQL_vanilla',
                    'tantor benchmark vanilla':'Tantor_vanilla', 'postgresql benchmark kernels':'PSQL_kernels',
                    'tantor benchmark kernels':'Tantor_kernels', 'linux_system_benchmark. UnixBench parsec':'UnixBench_parsec',
                    'postgresql benchmark balance':'PSQL_balance', 'freeipa authentication test':'FreeIPA_auth',
                    'Parsec impact fs benchmark':'Parsec_impact-fs', 'Parsec impact fs benchmark audit-off':'Parsec_imp-fs_aud-off',
                    'Apache_ReverseProxy':'Apache_RP', 'Steal time':'Steal_time', 'file system benchmark. EXFAT':'FS_EXFAT',
                    'FIO benchmark':'FIO', 'Virt UnixBench':'vUnixBench', 'vPingPong':'vPingPong', 'Steal time smolensk':'Steal_time-sm',
                    'postgresql benchmark oom':'PSQL_OOM', 'syslog-ng benchmark check-write-log':'Syslog-NG-cwl',
                    'DIGSIG. Check digsig time':'DIGSIG-cdt', 'docker web-application':'Docker-WA', 'ceph benchmark':'FS_CEPH',
                    'ceph fio benchmark':'FS_CEPH_fio', 'freeipa create users test':'FreeIPA_c-users',
                    'ceph parsec benchmark':'FS_CEPH_parsec', 'astra openvpn client connections':'AOpenVPNcc',
                    'dovecot benchmark':'Dovecot-IMAP', 'exim benchmark':'Exim4-SMTP', 'Large FIO benchmark':'FIO_large',
                    'Network benchmark. Init_on_free':'InitOnFree', 'segmentation_fault':'SegFault',
                    'postgresql benchmark olap':'PSQL_OLAP-hq',
                    'freeipa plugin test':'FreeIPA_plugin',
                    'XFS. Memory leak':'XFS_mem_leak'
                    }



#################################################################################################################################################
#Перечень соответствий релизов и build_version для генерации путей репозиториев
#################################################################################################################################################
def releases_dict():
    return get_allta_conf()['releases_dict']



#################################################################################################################################################
#Перечень ядер, используемых для отображения в списке ядер
#################################################################################################################################################
startswith_kernel_list = ['6.1', '6.6', '6.12', '5.15', '5.10']



#################################################################################################################################################
#Перечень снимков clonezilla 
#################################################################################################################################################
def cz_comm():
    return get_allta_conf()['cz_comm']



#################################################################################################################################################
#ALLTA version
#################################################################################################################################################
def allta_version():
    with open("ChangeLog", "r") as r:
        return r.readline().split(" ")[-1]
    


#################################################################################################################################################
#Найденные баги
#################################################################################################################################################
known_bugs = {
    "PostgreSQL": {
        "BT-51261": "https://jira.astralinux.ru/browse/BT-51261",
        "BT-37797": "https://jira.astralinux.ru/browse/BT-37797",
        "BT-40316": "https://jira.astralinux.ru/browse/BT-40316",
        "BT-35869": "https://jira.astralinux.ru/browse/BT-35869",
        "BT-48408": "https://jira.astralinux.ru/browse/BT-48408",
        "BT-61532": "https://jira.astralinux.ru/browse/BT-61532",
        "BT-76604": "https://jira.astralinux.ru/browse/BT-76604",
        "BT-96667": "https://jira.astralinux.ru/browse/BT-96667"
    },
    "Файловые системы": {
        "BT-38366": "https://jira.astralinux.ru/browse/BT-38366",
        "BT-54712": "https://jira.astralinux.ru/browse/BT-54712"
    },
    "Parsec": {
        "BT-61530": "https://jira.astralinux.ru/browse/BT-61530",
        "BT-52579": "https://jira.astralinux.ru/browse/BT-52579",
        "BT-69978": "https://jira.astralinux.ru/browse/BT-69978",
        "BT-92428": "https://jira.astralinux.ru/browse/BT-92428"
    },
    "Apache": {
        "BT-64331": "https://jira.astralinux.ru/browse/BT-64331"
    },
    "FreeIPA": {
        "BT-66518": "https://jira.astralinux.ru/browse/BT-66518",
        "BT-91580": "https://jira.astralinux.ru/browse/BT-91580"
    }
}



#################################################################################################################################################
#Аннотации к артефактам производительности
#################################################################################################################################################
unixbench_annotations = """
Падение производительности в 1.7.6 было вызвано ошибкой обновления правил аудита при astra-update -ATr. BT-61530.
"""
psql_annotations = """
Падение производительности в 1.7.6 было вызвано ошибкой обновления правил аудита при astra-update -ATr. BT-61530.
Падение производительности в 1.8.0 связано с блокировками. BT-51261.
"""
sys_service_annotations = """
Syslog-NG: 1.8.0-1.8.1.UU.2 - была ошибка в подсчете рейтинга, при необходимости переделать.
Auditd-files: 1.7.6-1.7.6.UU.2 - была ошибка в подсчете рейтинга, при необходимости переделать.
Небольшое увеличение рейтинга в 1.7.8 связано с переходом на новые сервера. Для удобства сравнения оставлены старые результаты.
"""
apache_annotations = """
Увеличение рейтинга x2 в 1.7.8 связано с переходом на новые сервера. Для удобства сравнения оставлены старые результаты.
"""
docker_annotations = """
Небольшое увеличение рейтинга в 1.7.8 связано с переходом на новые сервера. Для удобства сравнения оставлены старые результаты.
"""
freeipa_annotations = """
Небольшое увеличение рейтинга в 1.7.8 связано с переходом на новые сервера. Для удобства сравнения оставлены старые результаты.
"""
filesys_annotations = """
Увеличение рейтинга в 1.7.8 связано с переходом на новые сервера. Для удобства сравнения оставлены старые результаты.
"""
parsec_annotations = """
Небольшое увеличение рейтинга в 1.7.8 связано с переходом на новые сервера. Для удобства сравнения оставлены старые результаты.
"""
virt_annotations = """
Увеличение рейтинга в 1.7.8 связано с переходом на новые сервера. Для удобства сравнения оставлены старые результаты.
"""

annotations = {
    "Apache": apache_annotations,
    "Docker": docker_annotations,
    "FreeIPA": freeipa_annotations,
    "Parsec": parsec_annotations,
    "PostgreSQL": psql_annotations,
    "Qemu/KVM/Libvirt": virt_annotations,
    "UnixBench": unixbench_annotations,
    "Системные службы": sys_service_annotations,
    "Файловые системы": filesys_annotations
}



#################################################################################################################################################
#Список существующих сервисов и микросервисов ALLTA
#################################################################################################################################################
allta_services_list = [
    'acs.service', 
    'allta_auth.service',
    'allta_infocollector.service',
    'allta.service',
    'allta_vm.service',
    #'bot_allta.service',
    'changelog.service',
    'devpi.service',
    'grafana_prometheus.service',
    'node_exporter.service',
    'portainer.service',
    'statistics.service',
    'docker_registry.service'
]



#################################################################################################################################################
#API`s
#################################################################################################################################################
CONFIG_API_BASE = "https://allta.devos.astralinux.ru:21500/api/config/v1"
SERVER_API_BASE = "https://allta.devos.astralinux.ru:21501/api/server/v1"



#################################################################################################################################################
#Crede`s
#################################################################################################################################################
load_dotenv(dotenv_path='/var/allta_services/config/env.allta')
TOKEN = getenv("ALLTA_AUTH_API_KEY")
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
tokens = requests.get(f"{CONFIG_API_BASE}/config/tokens", headers=HEADERS, timeout=30, verify=False).json()
ilo = requests.get(f"{SERVER_API_BASE}/ilo/", headers=HEADERS, timeout=30, verify=False).json()



#################################################################################################################################################
#ACS
#################################################################################################################################################
SERVER_ACS_PORT = 9999
ACS_BASE_URL = f"http://{allta_network['ip']}:{SERVER_ACS_PORT}"
