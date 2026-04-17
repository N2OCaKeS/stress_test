import json

def analyze_ram_usage(filename="ram_usage_log.txt"):

    with open(filename, 'r') as f:
        lines = f.readlines()
    
    def get_memory_percent(line):
        parts = line.strip().split()
        print(parts)
        if len(parts) < 2:
            return None

        return float(parts[-2])
    
    first_10 = lines[:10]
    first_values = []
    for line in first_10:
        val = get_memory_percent(line)
        if val is not None:
            first_values.append(val)
    
    last_10 = lines[-10:]
    last_values = []
    for line in last_10:
        val = get_memory_percent(line)
        if val is not None:
            last_values.append(val)
    
    avg_first = sum(first_values) / len(first_values)
    avg_last = sum(last_values) / len(last_values)


    diff_avg = avg_last - avg_first
    if avg_first > 0:
        percent_increase = (diff_avg / avg_first) * 100
        if percent_increase < 0:
            percent_increase = 0
    else:
        percent_increase = 0
    

    if percent_increase > 20:
        status = "Присутсвует"
    else:
        status = "Отсутствует"
    
    data = {
        "status": status
    }

    with open("result.json", 'w', encoding="utf-8") as status_file:
        json.dump(data, status_file, ensure_ascii=False, indent=4)

    return status
    
            

if __name__ == "__main__":
    analyze_ram_usage("ram_usage_log.txt")