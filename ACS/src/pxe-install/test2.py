from src.utils.secondary_func import remote_cmd, remote_put_file

def fill_default_file(filename, stand_name):
    try:
        with open(filename, 'r') as file:
            lines = file.readlines()

        modified_lines = []
        for line in lines:
            if 'hostname=Test' in line:
                line = line.replace('hostname=Test', f'hostname={stand_name}')

            if 'url=http://<ip-address>/preseed.cfg' in line:
                line = line.replace(
                    'url=http://<ip-address>/preseed.cfg',
                    f'url=http://<ip-address>/{stand_name}/preseed.cfg'
                )
            modified_lines.append(line)
        
        with open("default", 'w') as file:
            file.writelines(modified_lines)
    except Exception as e:
        print(f"Произошла ошибка: {e}")

def fill_preseed_file(filename, stand_name):
    try:
        with open('preseed_template.cfg', 'r') as file:
            lines = file.readlines()

        with open('preseed.cfg', 'w') as file:
            for line in lines:
                if 'd-i mirror/http/directory string' in line:
                    line = line.replace(
                        '/frozen/1.7/1.7.5/1.7.5.9/installation/',
                        '/frozen/1.8/1.8.2/1.8.2.8/installation/'
                    )
                file.write(line)
    except Exception as e:
        pass

def test1(stand_name):
    """
        1. Удаляем все из директории Стенда
        2. Создаем pxelinux.cfg и внутри default
        3. Копируем правильно заполненный default
        4. Удаляем в /var/www/html/STAND_NAME/preseed.cfg
        5. Копируем правильно заполненный preseed.cfg
    """
    ### pxelinux.cfg/default
    remote_cmd(f"sudo rm -rf /srv/tftp/{stand_name}", host=..., user=..., passwd=...)
    remote_cmd(f"sudo mkdir /srv/tftp/{stand_name}/pxelinux.cfg", host=..., user=..., passwd=...)
    fill_default_file(stand_name=stand_name)
    remote_put_file()
    
    ### preseed.cfg
    remote_cmd(f"sudo rm -rf /var/www/html/{stand_name}", host=..., user=..., passwd=...)
    fill_preseed_file(stand_name=stand_name)
    remote_put_file(remote_path=f"/var/www/html/{stand_name}/preseed.cfg", local_path="preseed.cfg", host=..., user=..., passwd=...)
    

