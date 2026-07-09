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

    def settings(self, type_test="balance"):
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
                "web1": {
                    "set resov.conf": {
                        "command": f"sudo sh -c '{resolv}'",
                        "signal set": "",
                        "signal get": "",
                    },
                    "client settings": {
                        "command": client,
                        "signal set": "client",
                        "signal get": ["lbdb3", "lbdb3"],
                    },
                    "reboot": {
                        "signal set": "web1",
                        "signal get": ["client"],
                    },
                },
                'loader': {
                    "set resov.conf": {
                        "command": f"sudo sh -c '{resolv}'",
                        "signal set": "",
                        "signal get": "",
                    },
                    "client settings": {
                        "command": client,
                        "signal set": "client",
                        "signal get": ["web1", "web1"],
                    },
                    "reboot": {
                        "signal set": "loader",
                        "signal get": ["client"],
                    },
                }
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
        user_signals = []
        for n in range(3):
            tasks["dcfreeipa"][f"create user{n}"] = {
                "command": f'yes {DOMAIN_USER_PASSWORD}| ipa user-add user{n} --first=user{n} --last=user{n} --macmin=0 --macmax=3 --miclevel=63 --password --password-expiration="2099-12-31Z"',
                "signal set": f"user{n} created",
                "signal get": ["dcfreeipa", "Kinit"],
            }
            user_signals.append(f"user{n} created")
            tasks["dcfreeipa"][f"register database{n + 1}"] = {
                "command": f"ipa service-add postgres/database{n + 1}.{DOMAIN}@{DOMAIN.upper()}",
                "signal set": "",
                "signal get": ["dcfreeipa", "Kinit"],
            }

        if type_test == "info-sys":
            # ФСТЭК Приказ №17/№21, меры ИАФ.3, УПД.3: политика паролей для К1/УЗ1.
            # Только для info-sys: применяется ПОСЛЕ create userN (signal get на
            # user_signals), потому что DOMAIN_USER_PASSWORD = "1" не пройдёт
            # --minlength=12/--minclasses=3, если политика подействует раньше
            # ipa user-add. Блокировка по неверным попыткам (--maxfail/--lockouttime)
            # при этом всё равно действует для всех входов, начиная с этого момента.
            tasks["dcfreeipa"]["fstec password policy"] = {
                "command": (
                    "ipa pwpolicy-mod "
                    "--minlength=12 "       # минимальная длина пароля
                    "--minclasses=3 "       # минимум 3 класса символов (буквы, цифры, спецсимволы)
                    "--maxfail=3 "          # блокировка после 3 неверных попыток
                    "--lockouttime=1800 "   # время блокировки учётной записи — 30 минут
                    "--history=10 "         # запрет повторения последних 10 паролей
                    "--maxlife=90 "         # срок действия пароля — 90 дней
                    "--minlife=1"           # минимальный срок до смены — 1 день
                ),
                "signal set": "fstec pwpolicy",
                "signal get": ["dcfreeipa", "Kinit"] + user_signals,
            }

        tasks["dcfreeipa"]["add pgpool dns"] = {
            "command": f"ipa dnsrecord-add {DOMAIN} pgpool --a-rec={PGPOOL_IP}",
            "signal set": "pgpool dns",
            "signal get": ["dcfreeipa", "Kinit"],
        }

        tasks["dcfreeipa"][f"register apache"] = {
                "command": f"ipa service-add HTTP/web1.{DOMAIN}@{DOMAIN.upper()}",
                "signal set": "register apache",
                "signal get": ["dcfreeipa", "Kinit"],
        }

        # tasks["dcfreeipa"]["add apache dns"] = {
        #     "command": f"ipa dnsrecord-add {DOMAIN} web1 --a-rec={APACHE_IP}",
        #     "signal set": "apache dns",
        #     "signal get": ["dcfreeipa", "Kinit"],
        # }

        # ipa dnsrecord-add balance.rbt web1 --a-rec=192.168.100.219

        provider.execute(
            commands=tasks,
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )
