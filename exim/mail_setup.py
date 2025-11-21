import re
import subprocess

class Exim:
    def __init__(self):
        self.service_name = 'smtp'
        self.service_config_dir = f'/etc/exim4/'
        self.config_file = f"{self.service_config_dir}/exim4.conf"
        self.ip_local = ""
        self.update_config_file = f"{self.service_config_dir}/update-exim4.conf.conf"
        
    def get_default_config(self):
        config = {
            'dc_eximconfig_configtype': 'internet',
            'dc_other_hostnames': '',
            'dc_local_interfaces': f'{self.ip_local}',
            'dc_readhost': '',
            'dc_relay_domains': '',
            'dc_minimaldns': 'false',
            'dc_relay_nets': '',
            'dc_smarthost': '',
            'CFILEMODE': '644',
            'dc_use_split_config': 'true',
            'dc_hide_mailname': '',
            'dc_mailname_in_oh': 'true',
            'dc_localdelivery': 'maildir_home'
        }
        return config
    
    def write_config_file(self):
        config_content = "\n".join([f"{key}='{value}'" for key, value in self.get_default_config().items()]) + "\n"
        with open(self.update_config_file, 'w') as f:
            f.write(config_content)
    
    def update_exim_config(self):
        subprocess.run(['update-exim4.conf'], check=True)

    def restart_service(self):
        subprocess.run(['systemctl', 'restart', 'exim4'], check=True)
        
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

