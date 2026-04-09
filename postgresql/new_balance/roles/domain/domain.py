from new_balance.roles.vm_info import (
    DOMAIN,
    DOMAIN_ADMIN_PASSWORD,
    DOMAIN_ADMIN_USER,
    DOMAIN_USER_PASSWORD,
    PGPOOL_IP,
    VMS_DATES,
    VMS_GROUPS,
    USERNAME,
    PASSWORD,
    PROVIDER,
)
from allta import Libvirt, VBox


class DomainVM:
    def __init__(self):
        self.provider = PROVIDER

    def settings(self):
        """Полная настройка домена на всех ВМ"""
        provider = self.provider
        if isinstance(provider, VBox):
            domain = {
                "settings": {"domain": DOMAIN, "admin_password": DOMAIN_ADMIN_PASSWORD},
                "domain": {
                    "host": "dcfreeipa",
                },
                "client": {
                    # g_ если начинается с такого префикса то это для группы хостов
                    "host": "g_domain_client",
                },
            }

            provider.freeipa(
                domain=domain,
                vms_dates=VMS_DATES,
                vms_groups=VMS_GROUPS,
                username=USERNAME,
                password=PASSWORD,
            )

        elif isinstance(provider, Libvirt):
            client = f"sleep 10 && sudo DEBIAN_FRONTEND=noninteractive astra-freeipa-client -d {DOMAIN} -p {DOMAIN_ADMIN_PASSWORD} -y"
            resolv = f"""
sudo cat << 'EOF' > /etc/resolv.conf
search {DOMAIN}
nameserver {VMS_DATES["dcfreeipa"]["ip_bridge"]}
EOF"""

            freeipa = {
                "dcfreeipa": {
                    "init domain": {
                        "command": f"sudo DEBIAN_FRONTEND=noninteractive astra-freeipa-server -d {DOMAIN} -p {DOMAIN_ADMIN_PASSWORD} -y --ssl",
                        "signal set": "",
                        "signal get": "",
                    },
                },
            }
            provider.execute(
                commands=freeipa,
                vms_dates=VMS_DATES,
                vms_groups=VMS_GROUPS,
                username=USERNAME,
                password=PASSWORD,
            )
            freeipa = {
                "dcfreeipa": {
                    "reboot": {
                        "signal set": "dcfreeipa",
                        "signal get": "",
                    }
                },
                "database1": {
                    "set resov.conf": {
                        "command": f"sudo sh -c '{resolv}'",
                        "signal set": "",
                        "signal get": "",
                    },
                    "client settings": {
                        "command": f"sleep 60 && {client}",
                        "signal set": "client",
                        "signal get": ["dcfreeipa", "dcfreeipa"],
                    },
                    "reboot": {
                        "signal set": "database1",
                        "signal get": ["client"],
                    },
                },
                "database2": {
                    "set resov.conf": {
                        "command": f"sudo sh -c '{resolv}'",
                        "signal set": "",
                        "signal get": "",
                    },
                    "client settings": {
                        "command": client,
                        "signal set": "client",
                        "signal get": ["database1", "database1"],
                    },
                    "reboot": {
                        "signal set": "database2",
                        "signal get": ["client"],
                    },
                },
                "database3": {
                    "set resov.conf": {
                        "command": f"sudo sh -c '{resolv}'",
                        "signal set": "",
                        "signal get": "",
                    },
                    "client settings": {
                        "command": client,
                        "signal set": "client",
                        "signal get": ["database2", "database2"],
                    },
                    "reboot": {
                        "signal set": "database3",
                        "signal get": ["client"],
                    },
                },
                "lbdb1": {
                    "set resov.conf": {
                        "command": f"sudo sh -c '{resolv}'",
                        "signal set": "",
                        "signal get": "",
                    },
                    "client settings": {
                        "command": client,
                        "signal set": "client",
                        "signal get": ["database3", "database3"],
                    },
                    "reboot": {
                        "signal set": "lbdb1",
                        "signal get": ["client"],
                    },
                },
                "lbdb2": {
                    "set resov.conf": {
                        "command": f"sudo sh -c '{resolv}'",
                        "signal set": "",
                        "signal get": "",
                    },
                    "client settings": {
                        "command": client,
                        "signal set": "client",
                        "signal get": ["lbdb1", "lbdb1"],
                    },
                    "reboot": {
                        "signal set": "lbdb2",
                        "signal get": ["client"],
                    },
                },
                "lbdb3": {
                    "set resov.conf": {
                        "command": f"sudo sh -c '{resolv}'",
                        "signal set": "",
                        "signal get": "",
                    },
                    "client settings": {
                        "command": client,
                        "signal set": "client",
                        "signal get": ["lbdb2", "lbdb2"],
                    },
                    "reboot": {
                        "signal set": "lbdb3",
                        "signal get": ["client"],
                    },
                },
            }

            provider.execute(
                commands=freeipa,
                vms_dates=VMS_DATES,
                vms_groups=VMS_GROUPS,
                username=USERNAME,
                password=PASSWORD,
                timeout=60,
            )

        tasks = {
            "dcfreeipa": {
                "kinit": {
                    "command": f"yes {DOMAIN_ADMIN_PASSWORD} | kinit {DOMAIN_ADMIN_USER}",
                    "signal set": "Kinit",
                    "signal get": "",
                },
            }
        }

        # генератор словаря создает однотипные задачи в словарь
        for n in range(3):
            tasks["dcfreeipa"][f"create user{n}"] = {
                "command": f'yes {DOMAIN_USER_PASSWORD}| ipa user-add user{n} --first=user{n} --last=user{n} --macmin=0 --macmax=3 --miclevel=63 --password --password-expiration="2099-12-31Z"',
                "signal set": "",
                "signal get": ["dcfreeipa", "Kinit"],
            }
            tasks["dcfreeipa"][f"register database{n + 1}"] = {
                "command": f"ipa service-add postgres/database{n + 1}.{DOMAIN}@{DOMAIN.upper()}",
                "signal set": "",
                "signal get": ["dcfreeipa", "Kinit"],
            }

        tasks["dcfreeipa"]["add pgpool dns"] = {
            "command": f"ipa dnsrecord-add {DOMAIN} pgpool --a-rec={PGPOOL_IP}",
            "signal set": "pgpool dns",
            "signal get": ["dcfreeipa", "Kinit"],
        }

        provider.execute(
            commands=tasks,
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )
