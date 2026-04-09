import hashlib
from allta import Libvirt

PROVIDER = Libvirt()

with open("/etc/astra/build_version", "r") as f:
    version = f.read().strip()
VERSION_OS = ".".join(version.split(".")[:2])

VERSION_PG = "11"
if isinstance(PROVIDER, Libvirt):
    PGPOOL_IP = "192.168.100.5"

    if VERSION_OS == "1.7":
        VERSION_PG = "11"
        ETH_INTERFACE = "eth0"
    elif VERSION_OS == "1.8":
        VERSION_PG = "15"
        ETH_INTERFACE = "enp1s0"


# ОТЛАДКА
# VERSION_PG = '15'
# ETH_INTERFACE = 'enp1s0'
# VERSION_OS='1.8'

USERNAME = "u"
PASSWORD = "1"

VMS = ["database1", "database2", "database3", "lbdb1", "lbdb2", "lbdb3", "dcfreeipa"]

VMS_DATES = {
    "database1": {
        "host-port": "22",
        "cpu": "8",
        "ram": "32768",
    },
    "database2": {
        "host-port": "22",
        "cpu": "8",
        "ram": "32768",
    },
    "database3": {
        "host-port": "22",
        "cpu": "8",
        "ram": "32768",
    },
    "lbdb1": {
        "host-port": "22",
        "cpu": "8",
        "ram": "32768",
    },
    "lbdb2": {
        "host-port": "22",
        "cpu": "8",
        "ram": "32768",
    },
    "lbdb3": {
        "host-port": "22",
        "cpu": "8",
        "ram": "32768",
    },
    "dcfreeipa": {
        "host-port": "22",
        "cpu": "8",
        "ram": "32768",
    },
}

VMS_GROUPS = {
    "all": [
        "database1",
        "database2",
        "database3",
        "lbdb1",
        "lbdb2",
        "lbdb3",
        "dcfreeipa",
    ],
    "database": ["database1", "database2", "database3"],
    "load_balancer": ["lbdb1", "lbdb2", "lbdb3"],
    "replica": ["database2", "database3"],
    "domain_client": ["database1", "database2", "database3", "lbdb1", "lbdb2", "lbdb3"],
}


DOMAIN = "balance.rbt"
DOMAIN_ADMIN_USER = "admin"
DOMAIN_ADMIN_PASSWORD = "12345678"
DOMAIN_USER_PASSWORD = "1"

POSTGRES_PORT = "5440"
POSTGRES_DATA_PATH = f"/var/lib/postgresql/{VERSION_PG}/contrprimer"

PGPOOL_HOSTNAME = f"pgpool.{DOMAIN}"
PGPOOL_CONFIG_PATH = "/etc/pgpool2/pgpool.conf"
PGPOOL_PCP_USER = "pgpool"
PGPOOL_PASSWORD = "1"
PGPOOL_PASSWORD_MD5 = hashlib.md5((PGPOOL_PASSWORD).encode()).hexdigest()

PROVISION_PATH = "./provision/provision.sh"
