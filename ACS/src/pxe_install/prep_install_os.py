from src.utils.secondary_func import remote_cmd, remote_put_file, separate_astra_version


class PreparingForInstallationOS:
    def __init__(self, astra_build_version, stand_name):
        self.host = "10.177.103.202" #"host.docker.internal"
        self.user = ""
        self.passwd = ""
        self.port = 22
        self.astra_build_version = astra_build_version
        self.stand_name = stand_name

    def fill_default_or_grub_file(self, filename):
        try:
            with open(f"/fastapi_app/src/pxe_install/{filename}", 'r') as file:
                lines = file.readlines()

            modified_lines = []
            for line in lines:
                if 'hostname=Test' in line:
                    line = line.replace('hostname=Test', f'hostname={self.stand_name}')

                if 'url=http://<ip-address>/preseed.cfg' in line:
                    line = line.replace(
                        'url=http://<ip-address>/preseed.cfg',
                        f'url=http://10.177.103.202/{self.stand_name}/preseed.cfg'
                    )

                if "STAND_NAME" in line:
                    line = line.replace("STAND_NAME", self.stand_name)

                modified_lines.append(line)
            with open(f"/fastapi_app/src/pxe_install/{filename.replace("_template", "")}", 'w') as file:
                file.writelines(modified_lines)
        except Exception as e:
            print(f"Произошла ошибка: {e}")

    def fill_preseed_file(self, astra_build_version):
        astra_version = separate_astra_version(astra_build_version=astra_build_version)
        installation_repo = "installation-di" if astra_version['major_version'] == "1.8" else "installation"
        try:
            with open('/fastapi_app/src/pxe_install/preseed_template.cfg', 'r') as file:
                lines = file.readlines()

            with open('/fastapi_app/src/pxe_install/preseed.cfg', 'w') as file:
                for line in lines:
                    if 'd-i mirror/http/directory string' in line:
                        line = line.replace(
                            '/frozen/1.7/1.7.5/1.7.5.9/installation/',
                            f'/frozen/{astra_version['major_version']}/{astra_version['minor_version']}/{astra_version['build_version']}/{installation_repo}/'
                        )
                    file.write(line)
        except Exception as e:
            pass

    def prepare_old(self, uefi=True):
        """
            1. Удаляем все из директории Стенда
            2. Создаем pxelinux.cfg и внутри default (BIOS)
            2. sudo grub-mknetdir --net-directory=/srv/tftp --subdir=/{STAND_NAME}/boot/grub -d /usr/lib/grub/x86_64-efi
            3. Копируем правильно заполненный default (BIOS)
            3. Копируем правильно заполненный grub.cfg (UEFI)
            4. Удаляем в /var/www/html/STAND_NAME/preseed.cfg
            5. Копируем правильно заполненный preseed.cfg
            6. Копируем из netinst в директорию с именем стенда файлы linux и initrd.gz
        """
        remote_cmd(f"sudo rm -rf /srv/tftp/{self.stand_name}/*", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        if uefi:
            ### grub.cfg and everything necessary
            remote_cmd(f"sudo grub-mknetdir --net-directory=/srv/tftp --subdir=/{self.stand_name}/boot/grub -d /usr/lib/grub/x86_64-efi", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
            self.fill_default_or_grub_file(filename="grub_template.cfg")
            remote_cmd(f"sudo chown u:u /srv/tftp/{self.stand_name}/boot/grub", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
            remote_put_file(remote_path=f"/srv/tftp/{self.stand_name}/boot/grub/grub.cfg", local_path="/fastapi_app/src/pxe_install/grub.cfg", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
            remote_cmd(f"sudo chmod 755 /srv/tftp/{self.stand_name}/boot/grub/grub.cfg", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        else:
            ### pxelinux.cfg/default
            remote_cmd(f"sudo mkdir /srv/tftp/{self.stand_name}/pxelinux.cfg", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
            self.fill_default_or_grub_file(filename="default_template")
            remote_put_file(remote_path=f"/srv/tftp/{self.stand_name}/pxelinux.cfg/default", local_path="/fastapi_app/src/pxe_install/default", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        
        ### preseed.cfg
        remote_cmd(f"sudo rm -rf /var/www/html/{self.stand_name}/*", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        self.fill_preseed_file(astra_build_version=self.astra_build_version)
        remote_cmd(f"sudo chown u:u /var/www/html/{self.stand_name}", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        remote_put_file(remote_path=f"/var/www/html/{self.stand_name}/preseed.cfg", local_path="/fastapi_app/src/pxe_install/preseed.cfg", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        
        ### linux and initrd.gz
        remote_cmd(f"sudo chown u:u /srv/tftp/{self.stand_name}", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        remote_put_file(remote_path=f"/srv/tftp/{self.stand_name}/download_netints.py", local_path="/fastapi_app/src/pxe_install/download_netinst.py",
                        host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        
        """
            TODO -abv 1.7.7.9
        """
        remote_cmd(f"cd /srv/tftp/{self.stand_name}/ && sudo chmod +x download_netints.py && sudo python3 download_netints.py", 
                   host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        remote_cmd(f"sudo cp /srv/tftp/{self.stand_name}/netinst/linux /srv/tftp/{self.stand_name}/", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        remote_cmd(f"sudo cp /srv/tftp/{self.stand_name}/netinst/initrd.gz /srv/tftp/{self.stand_name}/", host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        remote_cmd(f"sudo rm -rf /srv/tftp/{self.stand_name}/netinst", host=self.host, user=self.user, passwd=self.passwd, port=self.port)

    
    def prepare(self, uefi=True):
        remote_cmd(f"rm -rf /home/u/{self.stand_name}/*", 
                   host=self.host, user=self.user, 
                   passwd=self.passwd, port=self.port)
        self.fill_preseed_file(astra_build_version=self.astra_build_version)
        remote_put_file(remote_path=f"/home/u/{self.stand_name}/preseed.cfg", 
                        local_path="/fastapi_app/src/pxe_install/preseed.cfg", 
                        host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        remote_put_file(remote_path=f"/home/u/{self.stand_name}/download_netinst.py", 
                        local_path="/fastapi_app/src/pxe_install/download_netinst.py",
                        host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        if uefi:
            self.fill_default_or_grub_file(filename="grub_template.cfg")
            remote_put_file(remote_path=f"/home/u/{self.stand_name}/grub.cfg", 
                            local_path="/fastapi_app/src/pxe_install/grub.cfg", 
                            host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        else:
            self.fill_default_or_grub_file(filename="default_template")
            remote_put_file(remote_path=f"/home/u/{self.stand_name}/default", 
                            local_path="/fastapi_app/src/pxe_install/default", 
                            host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        
        remote_put_file(remote_path=f"/home/u/{self.stand_name}/prep_install_os.sh", 
                        local_path="/fastapi_app/src/pxe_install/prep_install_os.sh",
                        host=self.host, user=self.user, passwd=self.passwd, port=self.port)
        remote_cmd(f"sudo bash /home/u/{self.stand_name}/prep_install_os.sh {self.stand_name} {self.astra_build_version}",
                   host=self.host, user=self.user, passwd=self.passwd, port=self.port)

            
