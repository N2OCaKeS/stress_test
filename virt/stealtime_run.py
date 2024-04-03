from virt_test import StealTime
from virt_conf import LOW, HIGH, TESTDIR


test = StealTime(rc_vbox='1.8.0.14',
                 vm_count=LOW,
                 testdir=TESTDIR,
                 load_type='low')

test.prepare_and_start()
test.vms_destroy()
if test.results_processing() == LOW:
    print('Low load test successfully done')

test.vm_count = HIGH
test.load_type = 'high'
test.prepare_and_start()
test.vms_destroy()
if test.results_processing() == HIGH:
    print('High load test successfully done')


