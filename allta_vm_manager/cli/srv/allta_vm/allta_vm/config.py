import requests
import json
import xml.etree.ElementTree as ET

def get_repo(rc):
    resp = requests.get('http://allta.devos.astralinux.ru/rest/api/get-repo-path')
    resp.raise_for_status()
    releases = resp.json()
    print("\n==> Парсим releases.json...")
    # 2. Берём нужный список deb-строк по self.rc
    try:
        sources_lines = releases[rc]
    except KeyError:
        raise ValueError(f"Нет записи для релиза '{rc}' в releases.json")

    # Собираем их в одну строку с настоящими переводами строк,
    # чтобы в /etc/apt/sources.list не попадали символы "\n"
    sources_str = "\n".join(sources_lines)
    return sources_str

def load_vms_dates(info_path):
    with open(info_path, "r", encoding="UTF-8") as f:
        json_string = f.read()
        vms_dates: dict = json.loads(json_string)
        return vms_dates

def edit_vm(xml_path: str, cpu: str, ram_mb: str):
    """
    Изменяет количество CPU и объем RAM в конфигурации ВМ
    с корректным сохранением XML структуры
    
    :param xml_path: Путь к XML файлу конфигурации ВМ
    :param cpu: Новое количество CPU (строка)
    :param ram_mb: Новый объем RAM в мегабайтах (строка)
    """
    # Зарегистрируем namespace если он есть в файле
    ET.register_namespace('libosinfo', "http://libosinfo.org/xmlns/libvirt/domain/1.0")
    
    # Парсим XML
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Изменяем CPU
    vcpu = root.find('vcpu')
    if vcpu is not None:
        vcpu.text = str(cpu)

    # Конвертируем RAM и изменяем
    ram_kb = str(int(ram_mb) * 1024)  # MB → KB

    # Обновляем memory и currentMemory
    for tag in ['memory', 'currentMemory']:
        element = root.find(tag)
        if element is not None:
            element.text = ram_kb
            element.set('unit', 'KiB')

    # Сохраняем с правильными параметрами
    tree.write(xml_path + ".mod", 
              encoding='UTF-8',
              xml_declaration=True,
              method='xml',
              short_empty_elements=False)
    return xml_path + ".mod"
