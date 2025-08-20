import datetime


from allta_image_conf import branches, cycle_tree_index, tests, parent_page_list, JIRA_URL


stand = 'stand1'
test_list = ['XFS', 'EXT4', 'NTFS', 'EXT4 parsec', 'postgresql', 'postgresql-sm', 'auditd-p', 'auditd-u', 'auditd-f', 'syslog-ng', 'unix']
KERNEL = ['5.15.0-70-generic', '5.10.176-1-generic', '5.15.0-70-lowlatency']


dates_list = [
    [['1.7.4', 'orel', '5.10.176-1-generic', 'stand1'], 'postgresql benchmark', 'PASS'], 
    [['1.7.4', 'orel', '5.15.0-70-generic', 'stand1'], 'file system benchmark. EXT4', 'NOT_EXECUTED'], 
    [['1.7.4', 'orel', '5.15.0-70-generic', 'stand1'], 'file system benchmark. XFS', 'PASS'], 
    [['1.7.4', 'orel', '5.15.0-70-generic', 'stand1'], 'postgresql benchmark', 'PASS'], 
    [['1.7.4', 'orel', '5.15.0-70-lowlatency', 'stand1'], 'postgresql benchmark', 'PASS']
]


class TestRunnerListPrepare():
    pass

class TestRunnerHandler():
    def __init__(self,
                 stand=stand,
                 testlist=test_list,
                 kernel=KERNEL):
        self.stand = stand
        self.test_list = testlist
        self.kernel = kernel
    
    def params(self):
        for i in range(0, len(dates_list)):  
            start_time = datetime.datetime.now().replace(microsecond=0)             
            print('-----' * 20)            
            print(f'Итерация № {i + 1}')            
            print(f'Прогресс выполнения - {int((i + 1) * 100 / len(dates_list))}%')            
            print(f'Время запуска: {start_time}\n')
            if dates_list[i][0][3] == self.stand:
                print(f'Cтенд: \033[92m{dates_list[i][0][3]}\033[0m')                
                if tests[dates_list[i][1]] in self.test_list:
                    if self.kernel:
                        for num in range(0, len(self.kernel)):
                            if dates_list[i][0][2] == self.kernel[num]:                           
                                print(f'Ядро: \033[92m{self.kernel[num]}\033[0m')                           
                                print(f'Тест: \033[92m{tests[dates_list[i][1]]}\033[0m')

class TestRunner():
    pass


test = TestRunnerHandler()

test.params()