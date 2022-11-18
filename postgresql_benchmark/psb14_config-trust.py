import os
import re
from psb_conf import PG_SETEST_CLUSTER

old_config_hba = '/etc/postgresql/14/'+ PG_SETEST_CLUSTER +'/pg_hba.conf'
new_config_hba = '/tmp/pg_hba.conf.new'

re1 = r'local\s*all\s*all\s*peer'
re2 = r'host\s*all\s*all\s*\d{1,3}.\d{1,3}.\d{1,3}.\d{1,3}\/\d{1,2}\s*scram-sha-256'

line_trust = 'local   all             all                                     trust\n'
line_host_trust = 'host    all             all             {ip}            trust\n'

with open(old_config_hba, 'r') as old_file_hba, open(new_config_hba, 'w') as new_file_hba:
    for line in old_file_hba:
        res_local = re.match(re1, line)
        res_host = re.match(re2, line)

        if res_local:
            new_file_hba.write(line_trust)
        elif res_host:
            ip = " ".join(res_host.group(0).split()).split(" ")[3]
            new_file_hba.write(line_host_trust.format(ip=ip))
        else:
            new_file_hba.write(line)

os.rename(old_config_hba, old_config_hba + '.old')
os.rename(new_config_hba, old_config_hba)