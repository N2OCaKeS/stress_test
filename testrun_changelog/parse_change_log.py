def extract_packages_from_table(soup, section_name, package_column=2):
    section = soup.find('a', {'name': section_name})
    if not section:
        return set()
    
    table = section.find_next('table')
    if not table:
        return set()
    
    packages = set()
    for row in table.find_all('tr')[1:]:
        cells = row.find_all('td')
        if len(cells) > package_column:
            package_name = cells[package_column].get_text(strip=True)
            if '(' in package_name:
                package_name = package_name.split('(')[0].strip()
            packages.add(package_name)

    return packages