import re

class Exim:
    def __init__(self):
        self.service_name = 'smtp'
        self.service_config_dir = f'/etc/exim4/'
        self.config_file = f"{self.service_config_dir}/exim4.conf"

    def set_configuration(self):
        pass


class Dovecot:
    def __init__(self):
        self.service_name = ""
        self.service_config_dir = "/etc/dovecot/"
        self.config_file = f"{self.service_config_dir}/dovecot.conf"
        self.auth_config_file = f"{self.service_config_dir}/conf.d/10-auth.conf"
        self.master_conf_file = f"{self.service_config_dir}/conf.d/master.conf"
        self.ssl_conf_file = f"{self.service_config_dir}/conf.d/10-ssl.conf"
        self.local_ip = f""

    def _add_settings_line(self):
        pass

    def _replace_settings(self, filename, pattern, replacement):
        with open(filename, 'r') as file:
            content = file.read()
    
        new_content = re.sub(pattern, replacement, content)
        with open(filename, 'w') as file:
            file.write(new_content)

    def set_configuration(self):
        self._replace_settings(filename=self.auth_config_file, pattern=r'^auth_mechanisms.*', replacement='auth_mechanisms = plain')
        self._replace_settings(filename=self.auth_config_file, pattern=r'^#disable_plaintext_auth.*', replacement='disable_plaintext_auth = no')
        # master.conf
        self._add_settings_line()
        self._replace_settings(filename=self.ssl_conf_file, pattern=r'ssl .*', replacement='ssl = no')

