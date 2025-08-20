def parse_fio_file(file_path):
    results = {
        'read': {'slat_avg': None, 'clat_avg': None, 'lat_avg': None, 'iops': None},
        'write': {'slat_avg': None, 'clat_avg': None, 'lat_avg': None, 'iops': None}
    }

    current_section = None

    with open(file_path, 'r') as file:
        for line in file:
            line = line.strip()
            
            if line.startswith('read:'):
                current_section = 'read'
                if 'IOPS=' in line:
                    iops_part = line.split('IOPS=')[1].split(',')[0]
                    results[current_section]['iops'] = float(iops_part)
            elif line.startswith('write:'):
                current_section = 'write'
                if 'IOPS=' in line:
                    iops_part = line.split('IOPS=')[1].split(',')[0]
                    results[current_section]['iops'] = float(iops_part)
            
            if current_section:
                if 'slat (' in line and 'avg=' in line:
                    parts = line.split('avg=')[1].split(',')[0]
                    results[current_section]['slat_avg'] = float(parts)

                elif 'clat (' in line and 'avg=' in line and 'percentiles' not in line:
                    parts = line.split('avg=')[1].split(',')[0]
                    results[current_section]['clat_avg'] = float(parts)
                
                elif line.startswith('lat (') and 'avg=' in line:
                    parts = line.split('avg=')[1].split(',')[0]
                    results[current_section]['lat_avg'] = float(parts)

    return results

if __name__ == "__main__":
    file_path = 'test_fio.txt'
    
    try:
        results = parse_fio_file(file_path)

        print(results)
        
        print("Read:")
        print(f"IOPS: {results['read']['iops']}")
        print(f"slat avg: {results['read']['slat_avg']} nsec")
        print(f"clat avg: {results['read']['clat_avg']} msec")
        print(f"lat avg: {results['read']['lat_avg']} msec")
        
        print("-"*50)
        
        print("Write:")
        print(f"IOPS: {results['write']['iops']}")
        print(f"slat avg: {results['write']['slat_avg']} nsec")
        print(f"clat avg: {results['write']['clat_avg']} msec")
        print(f"lat avg: {results['write']['lat_avg']} msec")
    
    except FileNotFoundError:
        print(f"файл '{file_path}' c результами не найден!")
    except Exception as e:
        print(f"произошла ошибка: {str(e)}")