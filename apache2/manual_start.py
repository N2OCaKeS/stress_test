#To Debug

from datetime import datetime

from libs.libapa import get_duration
from libs.libtests import ApacheBenchPam
from apa_conf import ABP_RAM, ABP_VCPU, ABP_VM_COUNT, SCRIPT_DIR



time_start_script = datetime.now()

abp = ApacheBenchPam(rc_name='1.7.10.72',
                    testdir=SCRIPT_DIR,
                    vm_count=ABP_VM_COUNT,
                    vcpu=ABP_VCPU,
                    ram=ABP_RAM,
                    kernel='6.1.161-1-generic')

abp.prepare_vms()
abp.create_test_env()
#abp.start_test()



lead_time = get_duration((datetime.now() - time_start_script).total_seconds())

print(f'Lead time: {lead_time}')