from new_balance.roles.vm_info import (
    VMS_DATES, VMS_GROUPS, USERNAME, PASSWORD, PROVIDER, DOMAIN, PGPOOL_HOSTNAME, POSTGRES_PORT
)

class ApacheVM:
    def __init__(self):
        self.provider = PROVIDER

    def settings(self):
        commands = {
            "g_web": {
                "enable apache2": {
                    "command": "sudo systemctl enable --now apache2",
                    "signal set": "",
                    "signal get": "",
                },
                "enable gssapi module": {
                    "command": "sudo a2enmod auth_gssapi && sudo systemctl restart apache2",
                    "signal set": "",
                    "signal get": "",
                },
            }
        }
        self.provider.execute(
            commands=commands,
            vms_dates=VMS_DATES,
            vms_groups=VMS_GROUPS,
            username=USERNAME,
            password=PASSWORD,
        )
