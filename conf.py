IP_CS = "192.168.0.108"
USER_CS = "u"
PASSWORD = "1"

REPO_DEBIAN_BUSTER_10 = "deb [trusted=yes] http://ftp.us.debian.org/debian/ buster main"
REPO_DEBIAN_BULLSEYE_11 = """deb [trusted=yes] http://deb.debian.org/debian bullseye main
deb [trusted=yes] http://security.debian.org/debian-security bullseye-security main
deb [trusted=yes] http://deb.debian.org/debian bullseye-updates main"""
REPO_DEBIAN_BOOKWORM_12 = """
deb [trusted=yes] https://deb.debian.org/debian bookworm main non-free-firmware
deb-src [trusted=yes] https://deb.debian.org/debian bookworm main non-free-firmware
"""
REPO_DRBL = "deb http://free.nchc.org.tw/drbl-core drbl stable"

KERNEL_VERSION = "5.10.0-34"

PATH_MACADDRS = "/home/u/clonezilla/macaddrs.txt"
PATH_IMAGES = "/home/partimag"

FIRST_ADDRESS_IN_LAST_OCTET = '17'