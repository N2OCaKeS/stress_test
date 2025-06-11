import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
# from src.utils.secondary_func import remote_put_file, remote_cmd, separate_astra_version


def separate_astra_version(astra_build_version):
    temp_version = astra_build_version.split(".")
    major_version = temp_version[0] + "." + temp_version[1]
    minor_version = major_version + "." + temp_version[2]
    return {
        "build_version": astra_build_version,
        "major_version": major_version,
        "minor_version": minor_version
    }

def get_folder_netinst(astra_build_version, download_dir="netinst"):
    
    astra_version = separate_astra_version(astra_build_version=astra_build_version)
    
    base_url = f"https://releases.devos.astralinux.ru/frozen/{astra_version['major_version']}/{astra_version['minor_version']}/{astra_version['build_version']}/installation/netinst/"

    os.makedirs(download_dir, exist_ok=True)
    def process_directory(url, current_dir):
        try:
            response = requests.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for link in soup.find_all('a'):
                href = link.get('href')
                if href == "../":
                    continue
                
                full_url = urljoin(url, href)
                
                if href.endswith('/'):
                    subdir = os.path.join(current_dir, href)
                    os.makedirs(subdir, exist_ok=True)
                    process_directory(full_url, subdir)
                else:
                    file_path = os.path.join(current_dir, href)
                    if not os.path.exists(file_path):
                        print(f"Скачиваем: {full_url}")
                        with requests.get(full_url, stream=True) as r:
                            r.raise_for_status()
                            with open(file_path, 'wb') as f:
                                for chunk in r.iter_content(8192):
                                    f.write(chunk)
        except Exception as e:
            print(f"Ошибка при обработке {url}: {str(e)}")

    process_directory(base_url, download_dir)


get_folder_netinst(astra_build_version="1.7.7.9")