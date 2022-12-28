# STAND = {
#      1: ('stand1', '\033[31m'),
#      2: ('stand2', '\033[32m'),
#      3: ('serverlow', '\033[33m'),
#      4: ('serverhigh', '\033[34m'),
# }

'''
  Данные стендов
  @ число - просто порядковый номер
  @ ключ 'name' - имя стенда из файла hosts.yml (inventory file)
  @ ключ 'color' - цвет выделения имени стенда в терминале (чтобы не запутаться)
'''

STANDS = {
     1: {'name': 'stand1', 'color': '\033[31m'},
     2: {'name': 'stand2', 'color': '\033[32m'},
     3: {'name': 'serverlow', 'color':'\033[33m'},
     4: {'name': 'serverhigh', 'color':'\033[34m'},
}

'''
  Набор тестовых сценариев
  @ ключ 'name' - название .yml файла
  @ ключ 'path' - путь начиная от основной директории playbooks до файла .yaml
'''
TEST_SETS = {
    1: {'name': 'postgresql', 'path': 'postgresql/'},
    2: {'name': 'syslog-ng', 'path': 'syslog-ng/'},
    # 3: 'auditd',
    # 4: 'ext4',
    # 5: 'ntfs'
}

END_COLOR_LINE = '\033[0m'

'''
  Начало основного playbook для стенда
'''
# TODO Доработать передачу версии либо убрать в плэйбуки основных тестовых сценариев
START_FILE_MAIN_PLAYBOOK = """
---
- name: Main playbook "{{ HOST }}"
  hosts: "{{ HOST }}"
  vars:
    ASTRA_VERSION:  "1.7.2"

  tasks:

  - include: ../tasks/check_astra_verison.yml

"""