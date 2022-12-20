from psb_conf import DATA_SYSMON_FILENAME, REPORT_SYSMON_FILENAME


def create_avgsysmon_filereport():
    def find_avg(array):
        sort_array = list(zip(*array))
        x_to_float = [list(map(float, i)) for i in sort_array]
        x_avg_sum = ['{:.2f}'.format(sum(i)/len(sort_array[0])) for i in x_to_float ]
        return x_avg_sum    
    
    arr = []
    
    with open(f'{DATA_SYSMON_FILENAME}', 'r') as file:
        for line in file:
            arr.append(line.split())

    start = 0
    stop = 0

    f2 = open(f'{REPORT_SYSMON_FILENAME}', 'w')
    f2.close()

    for ind, value in enumerate(arr):
        if value[0] == "-----":
            stop = ind
            avg = find_avg(arr[start:stop])
            start = stop + 1
            f = open(f'{REPORT_SYSMON_FILENAME}', 'a')
            f.write(" ".join(avg))
            f.write('\n')
            f.close()


def sorted_data_from_sysmonfile():
    data = []
    with open(f'{DATA_SYSMON_FILENAME}', 'r') as file:
        for line in file:
            if line[0] not in "-----":
                data.append(line.split())
                
    data_sort = list(zip(*data))
    data_for_digit = [list(map(float, i)) for i in data_sort]

    x = []
    for i in range(len(data_for_digit[0])):
        x.append(i * 30)

    return data_for_digit, x

    
