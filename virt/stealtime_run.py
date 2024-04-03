from virt_test import StealTime
from virt_conf import TEST_MASHINES


test = StealTime(rc_vbox='1.8.0.14')

test.prepare()
test.run()
test.vms_off()
if test.results_processing() == TEST_MASHINES:
    print('Test successfully done')

