from libs.libtests import CreateVM, Test_1
from libs.libtable import Report
from ovpn_conf import REPORT_PATH, VMS_DATES, VMS_COUNT, BOX, RC
from public import public


rp = Report()

ovpn_test = Test_1(vbox=BOX,
                   vm_count=VMS_COUNT,
                   vms_dates=VMS_DATES,
                   rc_name="1.8.1.6")
# libvirt-install
#ovpn_test.common_build()
#ovpn_test.provision()
#ovpn_test.start()

# vagrant-libvirt.old
#ovpn_test.vms_destroy()
#ovpn_test.prepare_vms()
#ovpn_test.start_test()


print("RESULTS NEXT STAGE")
# rp.build()
# rp.pass_fail()
#rp.plot_waves()
public.run_publish()

