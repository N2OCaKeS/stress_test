from time import perf_counter
from ovpn.ovpn_vm import Ovpn
from libs.libpublic import ovpn_publisher

def _fmt_duration(seconds: float) -> str:
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, sec = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{sec:02d}"

start_ts = perf_counter()
rc = "1.8.1.6"
mode = "o"
ovpn = Ovpn()
ovpn.build(rc, mode)
ovpn.provision()
ovpn.server_settings()
ovpn.start_test()
result = ovpn.get_result()
lead_time_text = _fmt_duration(perf_counter() - start_ts)
ovpn_publisher(username="",
                token="ghp_XXXXXXXXXXXXXXXXXXXX",
                title="",
                stats=result,
                space="",
                parent_title="",
                lead_time=lead_time_text
               )
