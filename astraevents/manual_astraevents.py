from allta import SystemCommands
from libs.libtests import AstraEventsLoadTest
from aeb_conf import BASE_PATH, VCPU_MIN, RAM_MIN, VCPU_MAX, RAM_MAX, VM_COUNT


TEST_CYCLE_VERSION = "1.8.6.39"
CONF_PARENT_PAGE = f"STRESS {TEST_CYCLE_VERSION} astraevents"
CONF_NEW_PAGE_NAME = f"stress_{TEST_CYCLE_VERSION}_astraevents"

KERNEL = "6.1.166-1-generic"

astra_events_load_test = AstraEventsLoadTest(rc_name=TEST_CYCLE_VERSION, 
                                             testdir=BASE_PATH,
                                             vm_count=VM_COUNT, 
                                             kernel=KERNEL,
                                             vcpu_min=VCPU_MIN,
                                             ram_min=RAM_MIN,
                                             vcpu_max=VCPU_MAX,
                                             ram_max=RAM_MAX)
astra_events_load_test.prepare_vms()
astra_events_load_test.start_test()
astra_events_load_test.results_processing()

