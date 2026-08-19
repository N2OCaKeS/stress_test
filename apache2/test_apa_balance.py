from datetime import datetime

from libs.libtests import ApacheBalance
from libs.libapa import get_duration
from libs.libpublic_new import apache_balance_publisher
from apa_conf import SCRIPT_DIR, A_BALANCE_VM_COUNT, A_BALANCE_VCPU, A_BALANCE_RAM


TEST_CYCLE_VERSION = "1.7.11.44"
KERNEL = ""


time_start_script = datetime.now()
lead_time = get_duration((datetime.now() - time_start_script).total_seconds())
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
total_rating = apache_balance.preprocessing_results()


apache_balance_publisher(
    username="",
    token="",
    space="",
    parent_title="",
    title="",
    stand_number="",
    total_rating=total_rating,
    lead_time=lead_time,
    test_cycle_version=TEST_CYCLE_VERSION,
)
