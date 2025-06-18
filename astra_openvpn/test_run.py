from libs.libtests import Ovpn20k
from libs.libtable import Report
from ovpn_conf import REPORT_PATH

rp = Report()

#pingpong_test = PingPong(rc_vbox=args.VBOX,
#                         vm_count=1,
#                         testdir=REPORT_PATH,
#                         kernel=str(args.TCYC).split('_')[2],
#                         vcpu = 8,
#                         ram=16384)

ovpn_test = Ovpn20k(rc_vbox="1.7.5",
                         vm_count=4,
                         testdir=REPORT_PATH,
                         kernel="6.1.90-1-generic",
                         vcpu=3,
                         ram=25000)
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

#received, sent = rp.bytes_counter()
#print("Суммарное кол-во байт:", f"Получено: {received}", f"Отправлено: {sent}", sep="\n\n")
