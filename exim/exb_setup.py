import re
import os
import pwd
import hashlib
import subprocess


class Exim:
    def __init__(self):
        self.service_name = 'smtp'
        self.service_config_dir = f'/etc/exim4'
        self.config_file = f"{self.service_config_dir}/exim4.conf"
        self.update_config_file = f"{self.service_config_dir}/update-exim4.conf.conf"
        self.verify_macros_file = f"{self.service_config_dir}/exim4.conf.localmacros"
        self.acs_virify_file = f"{self.service_config_dir}/conf.d/acs/30_exim4-config_check_rcpt"
        
    def get_default_config(self):
        config = {
            'dc_eximconfig_configtype': 'internet',
            'dc_other_hostnames': 'stress-testing.local',
            'dc_local_interfaces': '',
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
    
    def _add_verify_acs(self):
        try:
            with open(self.acs_virify_file, 'r', encoding='utf-8') as file:
                lines = file.readlines()
            
            new_lines = []
            i = 0
            replaced = False
            
            while i < len(lines):
                if lines[i].strip() == 'acl_check_rcpt:' and not replaced:
                    new_lines.append('acl_check_rcpt:\n')
                    new_lines.append('deny\n')
                    new_lines.append('    domains = +local_domains\n')
                    new_lines.append('    message = unknown user\n')
                    new_lines.append('    !verify = recipient\n')

                    i += 6
                    replaced = True
                    continue
                else:
                    new_lines.append(lines[i])
                    i += 1
            
            if replaced:
                with open(self.acs_virify_file, 'w', encoding='utf-8') as file:
                    file.writelines(new_lines)
                print(f"Блок успешно заменен в файле {self.acs_virify_file}")
                return True
            else:
                print(f"Блок acl_check_rcpt не найден в файле {self.acs_virify_file}")
                return False
                
        except Exception as e:
            print(f"Ошибка: {str(e)}")
            return False


    def _add_verify_macros(self):
        with open(self.verify_macros_file, "w") as f:
            f.write("CHECK_RCPT_VERIFY_RECIPIENT = yes")
    
    def write_config_file(self):
        self._add_verify_acs()
        self._add_verify_macros()
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
        self.service_config_dir = "/etc/dovecot"
        self.config_file = f"{self.service_config_dir}/dovecot.conf"
        self.auth_config_file = f"{self.service_config_dir}/conf.d/10-auth.conf"
        self.master_conf_file = f"{self.service_config_dir}/conf.d/10-master.conf"
        self.ssl_conf_file = f"{self.service_config_dir}/conf.d/10-ssl.conf"
        self.local_ip = f""

    def restart_service(self):
        subprocess.run(['systemctl', 'restart', 'dovecot.service'], check=True)

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
        self._replace_settings(filename=self.auth_config_file, pattern=r'#?\s*disable_plaintext_auth.*', replacement='disable_plaintext_auth = no')
        with open(self.master_conf_file, 'r') as file:
            lines = file.readlines()

        new_lines = []
        for line in lines:
            new_lines.append(line)
            if 'service auth {' in line:
                new_lines.append('  unix_listener auth-client {\n')
                new_lines.append('    mode = 0600\n')
                new_lines.append('    user = Debian-exim\n')
                new_lines.append('  }\n')

        with open(self.master_conf_file, 'w') as file:
            file.writelines(new_lines)
        self._replace_settings(filename=self.ssl_conf_file, pattern=r'ssl .*', replacement='ssl = no')
        self.restart_service()


class CreateMailUsers:
    def __init__(self, user_count):
        self.user_count = user_count

    def set_configuration_single_user(self, ind=0):
        username = f"user{ind}"
        mail_dir = f"/var/mail/{username}"


        system_commands = [
            ["sudo", "useradd", "-m", "-d", mail_dir, "-s", "/bin/false", username],
            ["sudo", "usermod", "-p", f"{hashlib.md5('1'.encode()).hexdigest()}", username]
        ]

        for cmd in system_commands:
            result = subprocess.run(cmd, capture_output=True, text=True)

        user_info = pwd.getpwnam(username)
        uid, gid = user_info.pw_uid, user_info.pw_gid

    def set_configuration(self):
        for ind in range(1, self.user_count + 1):
            self.set_configuration_single_user(ind)


if __name__ == "__main__":
    d = Dovecot()
    ex = Exim()
    cu = CreateMailUsers(user_count=10)
    ex.write_config_file()
    ex.update_exim_config()
    ex.restart_service()
    d.set_configuration()
    d.restart_service()
    cu.set_configuration()


    