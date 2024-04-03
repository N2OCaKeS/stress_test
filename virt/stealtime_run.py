from virt_test import StealTime
from virt_conf import TEST_MASHINES, TESTDIR


test = StealTime(rc_vbox='1.8.0.14',
                 vm_count=TEST_MASHINES,
                 testdir=TESTDIR)

test.prepare_and_start()
test.vms_off()
if test.results_processing() == TEST_MASHINES:
    print('Test successfully done')

