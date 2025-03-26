import re
from bs4 import BeautifulSoup

def extract_packages_from_table(soup, section_name, package_column=2):
    """Извлекает пакеты из указанной секции
       package_column - номер столбца с названиями пакетов (начиная с 0)
    """
    section = soup.find('a', {'name': section_name})
    if not section:
        return set()
    
    table = section.find_next('table')
    if not table:
        return set()
    
    packages = set()
    # duplicates = set()
    for row in table.find_all('tr')[1:]:  # Пропускаем заголовок
        cells = row.find_all('td')
        if len(cells) > package_column:
            package_name = cells[package_column].get_text(strip=True)
            # Удаляем версию из названия, если она есть в скобках
            if '(' in package_name:
                package_name = package_name.split('(')[0].strip()
            packages.add(package_name)
    
    # duplicates = set()
    # seen = set()

    # for item in packages:
    #     if item in seen:
    #         duplicates.add(item)
    #     else:
    #         seen.add(item)

    # print(list(duplicates))

    return packages



# def extract_package_names(html_content):
#     soup = BeautifulSoup(html_content, 'html.parser')
#     package_names = set()
    
#     # Ищем все таблицы в документе
#     tables = soup.find_all('table')
    
#     for table in tables:
#         # Определяем индекс столбца с названием пакета
#         headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]
        
#         # Определяем где находится столбец с пакетом
#         if 'Package' in headers:
#             col_index = headers.index('Package')
#         elif 'source' in headers:
#             col_index = headers.index('source')
#         else:
#             # Если не нашли явных заголовков, предполагаем первый столбец
#             col_index = 0
        
#         # Ищем все строки в таблице (кроме заголовков)
#         rows = table.find_all('tr')[1:]  # Пропускаем первую строку с заголовками
        
#         for row in rows:
#             cols = row.find_all('td')
#             if len(cols) > col_index:
#                 package_with_version = cols[col_index].get_text(strip=True)
                
#                 # Удаляем версию из названия пакета
#                 # Обрабатываем разные форматы:
#                 # 1) Название (версия)
#                 # 2) Название версия
#                 # 3) Название-версия
#                 package_name = re.sub(
#                     r'\s*\([^)]*\)$|'      # Удаляем (версия) в конце
#                     r'\s*[\d.+-]+.*$|',     # Удаляем версию после пробела
#                     '', 
#                     package_with_version
#                 ).strip()
                
#                 if package_name:
#                     package_names.add(package_name)
    
#     return sorted(package_names)