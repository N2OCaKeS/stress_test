import os
import re
import sys
import requests
from conf import REPO_DEBIAN_BUSTER_10, REPO_DRBL, REPO_DEBIAN_BULLSEYE_11, KERNEL_VERSION

def check_ext_exist_repo():
    exist = False
    with open("/etc/apt/sources.list", "r") as sl_file:
        repos_text = sl_file.readlines()
        for line in repos_text:
            if "extended" in line and "#" not in line:
                exist = True
    return exist

def check_astra_version():
    with open("/etc/astra/build_version", "r") as bv_file:
        build_version = bv_file.readline()
        if build_version.startswith("1.7"):
            return True
        else:
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

def install_packages(pkgs):
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
    
def change_kernel_default(uuid="cc5b7c98-6dec-4b0c-96f3-2b80f71bddf7"):
    file_path = "/etc/default/grub"
    with open(file_path, "r") as f:
        lines = f.readlines()
    with open(file_path, "w") as file:
        for line in lines:
            if "GRUB_DEFAULT" in line:
                line.strip()
                file.write("#" + line)
                file.write(f"GRUB_DEFAULT=gnulinux-{KERNEL_VERSION}-amd64-advanced-{uuid}\n")
            else:
                file.write(line)
    os.system("update-grub")
    

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
        print(f"{RED}Указана версия не 1.7, а скрипт для 1.7!{RESET}")
        sys.exit(1)
    
    write_repo_in_sources_list(REPO_DEBIAN_BUSTER_10)
    write_repo_in_sources_list(REPO_DRBL)
    wget_key_drbl()
    install_packages(pkgs="ipcalc drbl etherwake disktype udpcast txt2html chntpw nis discover clonezilla")
    comment_repo(repo=REPO_DEBIAN_BUSTER_10)
    write_repo_in_sources_list(REPO_DEBIAN_BULLSEYE_11)
    install_packages(pkgs=f"linux-image-{KERNEL_VERSION}-amd64")
    change_kernel_default()
    comment_repo(repo=REPO_DEBIAN_BULLSEYE_11)
    write_repo_in_sources_list(REPO_DEBIAN_BUSTER_10)