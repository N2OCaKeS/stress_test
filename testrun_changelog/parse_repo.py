def parse_packages(text):
    packages = []
    current_pkg = {}
    
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('Package:'):
            if current_pkg:  # Если уже есть данные о пакете, сохраняем их
                packages.append(current_pkg)
                current_pkg = {}
            current_pkg['name'] = line.split(':', 1)[1].strip()
        elif line.startswith('Depends:') and 'name' in current_pkg:
            depends = line.split(':', 1)[1].strip()
            # Упрощаем зависимости, убирая версии
            depends_list = [d.split(' (')[0].strip() for d in depends.split(',')]
            current_pkg['depends'] = depends_list
    
    if current_pkg:  # Добавляем последний пакет
        packages.append(current_pkg)
    
    return packages