import logging
from allta import SystemCommands, Libvirt
from allta_vm.libs.exit_code import ExitCodes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

def _rc_name(code: int) -> str:
    try:
        return ExitCodes(code).name
    except Exception:
        return str(code)

def _log_and_return(where: str, level: int, msg: str, code: int) -> int:
    logging.log(level, "%s: %s (exit=%s)", where, msg, _rc_name(code))
    return code

class Server:
    @staticmethod
    def __install_deps() -> int:
        where = "server.install_deps"
        try:
            base = "sudo apt-get update && sudo apt-get install -y "
            deps = (
                "build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev "
                "libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev libffi-dev strace "
                "python3-requests sshpass tar wget bridge-utils "
            )

            try:
                version_os = SystemCommands.check_output_command("cat /etc/astra_version").strip()
            except Exception:
                return _log_and_return(where, logging.ERROR, "cannot read /etc/astra_version", ExitCodes.PRECHECK_SERVICE)

            if version_os.startswith("1.8"):
                deps += "linux-tools-6.1*-generic linux-tools-6.6*-generic "
            elif version_os.startswith("1.7"):
                deps += (
                    "linux-tools-5.10*-generic linux-tools-5.15*-generic linux-tools-common-5.15* "
                    "linux-tools-5.15*-lowlatency libssl1.1 psmisc "
                )
            else:
                logging.warning("%s: unknown astra version '%s', continue with base deps only", where, version_os)

            rc_apt = SystemCommands.cmd_with_returncode(base + deps)
            if rc_apt != 0:
                return _log_and_return(where, logging.ERROR, "apt install failed", ExitCodes.ACTION_FAILED)

            try:
                rc_prep = Libvirt.prepare()
                SystemCommands.cmd_with_returncode("sudo docker run -d --name node_exporter --net=host prom/node-exporter:latest")
            except Exception:
                rc_prep = ExitCodes.UNEXPECTED
            if rc_prep != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR, f"Libvirt.prepare failed rc={_rc_name(rc_prep)}", ExitCodes.ACTION_FAILED)

            logging.info("%s: OK", where)
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED


    @staticmethod
    def server_init(phy_if: str, ip: str) -> int:
        where = "server.init"
        try:
            rc_deps = Server.__install_deps()
            if rc_deps != ExitCodes.OK:
                return _log_and_return(where, logging.ERROR, f"deps failed rc={_rc_name(rc_deps)}", rc_deps)


            logging.info("%s: OK", where)
            return ExitCodes.OK
        except Exception:
            logging.exception("%s crashed", where)
            return ExitCodes.UNEXPECTED
