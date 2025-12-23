import subprocess

class FreeIPALdcltManager:
    def __init__(self, server, bind_dn, password=None, base_domain="stress-testing.local"):
        self.server = server
        self.bind_dn = bind_dn
        self.password = password
        self.base_domain = base_domain
        self.base_dn = f"cn=users,cn=accounts,dc={base_domain.replace('.', ',dc=')}"

    def run_ldclt_command(self, cmd_args, show_output=True):
        if show_output:
            print(f"Выполняем: {' '.join(cmd_args)}")
        
        process = subprocess.Popen(
            cmd_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            universal_newlines=True
        )
        
        stdout, stderr = process.communicate()
        
        if process.returncode != 0:
            print(f"Ошибка выполнения ldclt :{process.returncode}")
            if stderr:
                print(f"Ошибка: {stderr}")
            return False, stdout, stderr
        else:
            if show_output and stdout:
                print(f"Вывод: {stdout}")
            return True, stdout, stderr
        
    def create_users_simple(self, num_users=10, start_uid=10000):
        cmd = [
            'ldclt',
            '-h', self.server,
            '-D', self.bind_dn,
            '-w', self.password,
            '-b', self.base_dn,
            '-e', 'add,person',
            '-e', f'rdn=uid=user\\%d',
            '-e', f'esuffix={self.base_dn}',
            '-e', f'attr=cn:Test User \\%d',
            '-e', f'attr=sn:User\\%d',
            '-e', f'attr=givenName:Test\\%d',
            '-e', f'attr=uidNumber:\\%d',
            '-e', f'inc:uidNumber:{start_uid}',
            '-e', f'attr=gidNumber:\\%d',
            '-e', f'inc:gidNumber:{start_uid}',
            '-e', f'attr=homeDirectory:/home/user\\%d',
            '-e', f'attr=loginShell:/bin/bash',
            '-e', f'attr=mail:user\\%d@{self.base_domain}',
            '-e', f'attr=userPassword:12345678\\!',
            '-e', f'attr=objectClass:top',
            '-e', f'attr=objectClass:person',
            '-e', f'attr=objectClass:organizationalperson',
            '-e', f'attr=objectClass:inetorgperson',
            '-e', f'attr=objectClass:posixaccount',
            '-e', f'attr=objectClass:ipaobject',
            '-e', f'attr=displayName:Test User \\%d',
            '-N', str(num_users),
            '-v'
        ]
        
        status, stdout, stderr = self.run_ldclt_command(cmd)
        
        if status:
            print(f"\nУспешно создано {num_users} пользователей")
        else:
            print(f"\nОшибка при создании пользователей")
        
        return status
    

if __name__ == "__main__":
    config = {
        'server': 'virtual-station1.stress-testing.local',
        'bind_dn': 'uid=admin,cn=users,cn=accounts,dc=stress-testing,dc=local',
        'base_domain': 'stress-testing.local',
        'num_users': 10,
        'start_uid': 100
    }

    manager = FreeIPALdcltManager(
        server=config['server'],
        bind_dn=config['bind_dn'],
        base_domain=config['base_domain']
    )
    status = manager.create_users_simple(
        num_users=config['num_users'],
        start_uid=config['start_uid']
    )

    
                