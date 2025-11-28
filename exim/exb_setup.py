import re
import os
import pwd
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


class CreateMailUsers:
    def __init__(self, user_count):
        self.user_count = user_count

    def set_configuration_single_user(self, ind=0):
        username = f"user{ind}"
        mail_dir = f"/var/mail/{username}"
        system_commands = [
            ["sudo", "useradd", "-m", "-d", mail_dir, "-s", "/bin/false", username],
            ["sudo", "usermod", "-p", "$(openssl passwd -1 '1')", username]
        ]

        for cmd in system_commands:
            result = subprocess.run(cmd, capture_output=True, text=True)

        directories = [
            mail_dir,
            f"{mail_dir}/.Maildir",
            f"{mail_dir}/.Maildir/cur",
            f"{mail_dir}/.Maildir/new", 
            f"{mail_dir}/.Maildir/tmp"
        ]

        user_info = pwd.getpwnam(username)
        uid, gid = user_info.pw_uid, user_info.pw_gid

        for directory in directories:
            if not os.path.exists(directory):
                os.makedirs(directory, mode=0o700)
                os.chown(directory, uid, gid)

    def set_configuration(self):
        for ind in range(1, self.user_count + 1):
            self.set_configuration_single_user(ind)


