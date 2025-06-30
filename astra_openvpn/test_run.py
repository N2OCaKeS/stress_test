from libs.libtests import Ovpn20k 
from libs.libstests44 import CreateVM, Test_1
from libs.libtable import Report
from ovpn_conf import REPORT_PATH, VMS_DATES, VMS_COUNT


rp = Report()

ovpn_test = Test_1(vbox="1.7.5.o",
                   vm_count=VMS_COUNT,
                   vms_dates=VMS_DATES,
                   testdir=REPORT_PATH)
# libvirt-install
#ovpn_test.common_build()
#ovpn_test.provision()
#ovpn_test.start()

# vagrant-libvirt
#ovpn_test.vms_destroy()
#ovpn_test.prepare_vms()
#ovpn_test.start_test()





rp.build()

con = rp.con()
discon = rp.discon()
print(f"{con} Клиентов подключилось.", f"{discon} Клиентов отключилось.", sep="\n")

if discon > con:
    print("В процессе теста отвалилось:", discon - con, "туннелей")
    print("FAIL")
else: print("PASS")


print(rp.bytes_counter())
print(rp.parse_all_logs())

received, sent = rp.bytes_counter()
print("Суммарное кол-во байт:", f"Получено: {received}", f"Отправлено: {sent}", sep="\n\n")
