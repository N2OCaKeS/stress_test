from virt_test import StealTime
from virt_conf import LOW, HIGH, TESTDIR

box = '1.8.0.14'

low_load_test = StealTime(rc_vbox=box,
                          vm_count=LOW,
                          testdir=TESTDIR,
                          load_type='low')

high_load_test = StealTime(rc_vbox=box,
                           vm_count=HIGH,
                           testdir=TESTDIR,
                           load_type='high')


low_load_test.prepare_and_start()
low_load_test.vms_destroy()
if low_load_test.results_processing() == LOW:
    print('Low load test successfully done')

high_load_test.prepare_and_start()
high_load_test.vms_destroy()
if high_load_test.results_processing() == HIGH:
    print('High load test successfully done')


