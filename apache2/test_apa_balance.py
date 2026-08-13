from libs.libtests import ApacheBalance
from apa_conf import SCRIPT_DIR, A_BALANCE_VM_COUNT, A_BALANCE_VCPU, A_BALANCE_RAM


TEST_CYCLE_VERSION = "1.7.11.44"
KERNEL = ""


apache_balance = ApacheBalance(
    rc_name=TEST_CYCLE_VERSION,
    testdir=SCRIPT_DIR,
    vm_count=A_BALANCE_VM_COUNT,
    vcpu=A_BALANCE_VCPU,
    ram=A_BALANCE_RAM,
    kernel=KERNEL,
)

apache_balance.prepare_vms()
apache_balance.create_test_env()
apache_balance.start_test()
apache_balance.preprocessing_results()
