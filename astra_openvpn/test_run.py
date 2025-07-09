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
# ovpn_test.common_build()
# ovpn_test.provision()
# ovpn_test.start()

# vagrant-libvirt.old
#ovpn_test.vms_destroy()
#ovpn_test.prepare_vms()
#ovpn_test.start_test()


#print("RESULTS NEXT STAGE")
rp.build()
# print(rp.pass_fail())
# rp.pass_fail()
#public()


#rp.build()

#con = rp.con()
#discon = rp.discon()
#print(f"{con} Клиентов подключилось.", f"{discon} Клиентов отключилось.", sep="\n")

#if discon > con:
#    print("В процессе теста отвалилось:", discon - con, "туннелей")
#    print("FAIL")
#else: print("PASS")


#print(rp.bytes_counter())
#print(rp.parse_all_logs())

#received, sent = rp.bytes_counter()
#print("Суммарное кол-во байт:", f"Получено: {received}", f"Отправлено: {sent}", sep="\n\n")
