import os
import re
import sys
import argparse
import requests
import subprocess
from conf import REPO_DRBL, REPO_DEBIAN_BUSTER_10, REPO_DEBIAN_BULLSEYE_11, REPO_DEBIAN_BOOKWORM_12, KERNEL_VERSION_FOR_17, KERNEL_VERSION_FOR_18

DESCRIPTION = ""
parser = argparse.ArgumentParser(description=DESCRIPTION)
parser.add_argument('--uuid',
                    action='store',
                    required=True,
                    help='UUID storage',
                    dest='UUID')
args = parser.parse_args()


def check_ext_exist_repo():
    exist = False
    with open("/etc/apt/sources.list", "r") as sl_file:
        repos_text = sl_file.readlines()
        for line in repos_text:
            if "extended" in line and "#" not in line:
                exist = True
    return exist

def check_astra_version(ret_vers=None):
    with open("/etc/astra/build_version", "r") as bv_file:
        build_version = bv_file.readline()
        if build_version.startswith(("1.7", "1.8")):
            return build_version if ret_vers else True
        return False

def write_repo_in_sources_list(repo):
    with open("/etc/apt/sources.list", "a") as sourceslist_file:
        sourceslist_file.write(repo)
        sourceslist_file.write("\n")

def wget_key_drbl():
    url = "https://drbl.org/GPG-KEY-DRBL"
    destination = "/etc/apt/trusted.gpg.d/drbl-gpg.asc"

    try:
        response = requests.get(url)
        response.raise_for_status()
        
        with open(destination, 'wb') as f:
            f.write(response.content)
        
        print(f"Файл успешно сохранён в {destination}")
    except Exception as e:
        print(f"Произошла ошибка: {e}")
        print("Убедитесь, что скрипт запущен с правами root (sudo).")

def install_packages(pkgs='ipcalc drbl etherwake disktype udpcast txt2html chntpw nis discover clonezilla'):
    os.system("apt update")
    os.system(f"apt install -y {pkgs}")

def comment_repo(repo):
    file_path = "/etc/apt/sources.list"
    
    with open(file_path, "r") as file:
        lines = file.readlines()
    
    repos_to_comment = repo.split("\n") if "\n" in repo else [repo]
    
    updated_lines = []
    for line in lines:
        stripped_line = line.strip()
        if (stripped_line 
            and not line.lstrip().startswith("#")
            and any(rt.strip() == stripped_line for rt in repos_to_comment)
        ):
            updated_lines.append("#" + line)
        else:
            updated_lines.append(line)
    
    with open(file_path, "w") as file:
        file.writelines(updated_lines)
    
def change_kernel_default(kernel, uuid):
    file_path = "/etc/default/grub"
    with open(file_path, "r") as f:
        lines = f.readlines()
    with open(file_path, "w") as file:
        for line in lines:
            if "GRUB_DEFAULT" in line:
                line.strip()
                file.write("#" + line)
                file.write(f"GRUB_DEFAULT=gnulinux-{kernel}-amd64-advanced-{uuid}\n")
            else:
                file.write(line)
    os.system("update-grub")

def check_qty_interfaces():
    command = "ip link show | grep -c '^[0-9]'"
    result = subprocess.run(command, shell=True, stdout=subprocess.PIPE, text=True)
    count = int(result.stdout.strip())
    if count > 2:
        print(f"{RED}Количество интерфейсов больше: 2\nДолжен быть только lo и стандартный типо eth0{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    RED = "\033[91m"
    GREEN = "\033[92m"
    RESET = "\033[0m"
    if not check_ext_exist_repo():
        print(f"{RED}Не подключен extended репозиторий!{RESET}")
        sys.exit(1)
    else:
        print(f"{GREEN}Extended репозиторий на месте{RESET}")
    
    if not check_astra_version():
        print(f"{RED}Указана версия не 1.7 и не 1.8, а скрипт для 1.7 или для 1.8!{RESET}")
        sys.exit(1)

    check_qty_interfaces()
    
    write_repo_in_sources_list(REPO_DRBL)
    wget_key_drbl()
    
    if "1.7" in check_astra_version(ret_vers=True):
        write_repo_in_sources_list(REPO_DEBIAN_BUSTER_10)
        install_packages()
        comment_repo(repo=REPO_DEBIAN_BUSTER_10)
        write_repo_in_sources_list(REPO_DEBIAN_BULLSEYE_11)
        os.system("sudo apt-mark hold firmware-linux-free")
        os.system(f"sudo apt update && sudo apt install --no-install-recommends linux-image-{KERNEL_VERSION_FOR_17}-amd64")
        change_kernel_default(kernel=KERNEL_VERSION_FOR_17, uuid=args.UUID)
        comment_repo(repo=REPO_DEBIAN_BULLSEYE_11)
        write_repo_in_sources_list(REPO_DEBIAN_BUSTER_10)
    
    if "1.8" in check_astra_version(ret_vers=True):
        write_repo_in_sources_list(REPO_DEBIAN_BOOKWORM_12)
        install_packages()
        os.system(f"sudo apt update && sudo apt install --no-install-recommends linux-image-{KERNEL_VERSION_FOR_18}-amd64")
        change_kernel_default(kernel=KERNEL_VERSION_FOR_18, uuid=args.UUID)
    