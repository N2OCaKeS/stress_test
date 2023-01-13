'''
  Данные стендов
  @ число - просто порядковый номер
  @ ключ 'name' - имя стенда из файла hosts.yml (inventory file)
  @ ключ 'color' - цвет выделения имени стенда в терминале (чтобы не запутаться)
'''

STANDS = {
     1: {'name': 'stand1', 'color': '\033[36m'},
     2: {'name': 'stand2', 'color': '\033[32m'},
     3: {'name': 'servermiddle', 'color':'\033[33m'},
     4: {'name': 'serverhigh', 'color':'\033[31m' },
}

'''
  Набор тестовых сценариев
  @ ключ 'name' - название .yml файла
  @ ключ 'path' - путь начиная от основной директории playbooks до файла .yaml
'''
TEST_SETS = {
    1: {'name': 'postgresql', 'path': 'postgresql/'},
    2: {'name': 'syslog-ng', 'path': 'syslog-ng/'},
    3: {'name': 'psaud', 'path': 'auditd/'},
    4: {'name': 'fileaud', 'path': 'auditd/'},
    5: {'name': 'useraud', 'path': 'auditd/'},
    6: {'name': 'ext4', 'path': 'file_system/'},
    7: {'name': 'xfs', 'path': 'file_system/'},
    8: {'name': 'ntfs', 'path': 'file_system/'},
}

END_COLOR_LINE = '\033[0m'

'''
  Начало основного playbook для стенда
'''
START_FILE_MAIN_PLAYBOOK = """
---
- name: Main playbook "{{ HOST }}"
  hosts: "{{ HOST }}"
"""