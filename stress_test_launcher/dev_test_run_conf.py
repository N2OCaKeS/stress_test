STAND = {
     1: 'stand1',
     2: 'stand2',
     3: 'serverlow',
     4: 'serverhigh',
}

TEST_SET = {
    1: 'psql',
    2: 'syslog-ng',
    3: 'auditd',
    4: 'ext4',
    5: 'ntfs'
}

START_FILE_MAIN_PLAYBOOK = """
---
- name: Main playbook "{{ HOST }}"
  hosts: "{{ HOST }}"
  vars:
    ASTRA_VERSION:  "1.7.2"

  tasks:

  - include: ../tasks/check_astra_verison.yml

"""