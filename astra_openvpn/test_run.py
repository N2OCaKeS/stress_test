from libs.libtests import Ovpn20k
from ovpn_conf import REPORT_PATH

#pingpong_test = PingPong(rc_vbox=args.VBOX,
#                         vm_count=1,
#                         testdir=REPORT_PATH,
#                         kernel=str(args.TCYC).split('_')[2],
#                         vcpu = 8,
#                         ram=16384)

ovpn_test = Ovpn20k(rc_vbox="1.7.5",
                         vm_count=2,
                         testdir=REPORT_PATH,
                         kernel="6.1.90-1-generic",
                         vcpu=8,
                         ram=16384)
ovpn_test.vms_destroy()
ovpn_test.prepare_vms()
ovpn_test.start_test()
