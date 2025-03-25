from bs4 import BeautifulSoup
import re

def parse_version_changes(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    result = {
        'fixed_bugs': [],
        'changelog': [],
        'upgraded_binaries': [],
        'downgraded_binaries': [],
        'rebuilt_binaries': []
    }
    
    # Парсинг Fixed bugs
    fixed_bugs_table = soup.find('a', {'name': 'Fixed_bugs'}).find_next('table')
    for row in fixed_bugs_table.find_all('tr')[1:]:  # Пропускаем заголовок
        cols = row.find_all('td')
        if len(cols) >= 3:
            result['fixed_bugs'].append({
                'package': cols[0].get_text(strip=True),
                'version': cols[1].get_text(strip=True),
                'bugfixes': cols[2].get_text(strip=True)
            })
    
    # Парсинг Changelog
    changelog_table = soup.find('a', {'name': 'Changelog'}).find_next('table')
    for row in changelog_table.find_all('tr')[1:]:  # Пропускаем заголовок
        cols = row.find_all('td')
        if len(cols) >= 3:
            result['changelog'].append({
                'package': cols[0].get_text(strip=True),
                'version': cols[1].get_text(strip=True),
                'changes': cols[2].get_text(strip=True)
            })
    
    # Парсинг Upgraded binaries
    upgraded_table = soup.find('a', {'name': 'Upgraded_binaries'}).find_next('table')
    for row in upgraded_table.find_all('tr')[1:]:  # Пропускаем заголовок
        cols = row.find_all('td')
        if len(cols) >= 3:
            result['upgraded_binaries'].append({
                'package': cols[0].get_text(strip=True),
                'old_version': cols[1].get_text(strip=True),
                'new_version': cols[2].get_text(strip=True)
            })
    
    # Парсинг Downgraded binaries
    downgraded_table = soup.find('a', {'name': 'Downgraded_binaries'}).find_next('table')
    for row in downgraded_table.find_all('tr')[1:]:  # Пропускаем заголовок
        cols = row.find_all('td')
        if len(cols) >= 3:
            result['downgraded_binaries'].append({
                'package': cols[0].get_text(strip=True),
                'old_version': cols[1].get_text(strip=True),
                'new_version': cols[2].get_text(strip=True)
            })
    
    # Парсинг Rebuilt binaries
    rebuilt_table = soup.find('a', {'name': 'Rebuilt_binaries'}).find_next('table')
    for row in rebuilt_table.find_all('tr')[1:]:  # Пропускаем заголовок
        cols = row.find_all('td')
        if len(cols) >= 2:
            result['rebuilt_binaries'].append({
                'package': cols[0].get_text(strip=True),
                'version': cols[1].get_text(strip=True) if len(cols) > 1 else ''
            })
    
    return result

# # Пример использования
# with open('version_changes.html', 'r', encoding='utf-8') as f:
#     html_content = f.read()

# parsed_data = parse_version_changes(html_content)

# # Вывод результатов
# print("Fixed bugs:")
# for item in parsed_data['fixed_bugs']:
#     print(f"  {item['package']} ({item['version']}): {item['bugfixes']}")

# print("\nUpgraded binaries:")
# for item in parsed_data['upgraded_binaries']:
#     print(f"  {item['package']}: {item['old_version']} -> {item['new_version']}")
# print(parsed_data['upgraded_binaries'][0]['package'])