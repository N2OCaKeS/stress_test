# from libs.libtable import Report
# from public import public
from ovpn.ovpn_vm import Ovpn

rc = "1.8.1.6"
mode = "o"
ovpn = Ovpn()
ovpn.build(rc, mode)
ovpn.provision()
ovpn.server_settings()
# ovpn.start_test()

# rp = Report()

#print("RESULTS NEXT STAGE")
# rp.build()
#rp.pass_fail()
#rp.plot_waves()
#public.run_publish()
