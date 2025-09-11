



stand3 = []
stand4 = []
stand10 = []
stand11 = []
stand12 = []
stand13 = []




topics = {
    'orel_stand3':      ['EXT2', 'EXT3', 'EXT4', 'FAT',  'EXFAT', 'XFS', 'FreeIPA auth', 'unix'],
    'smolensk_stand3':  ['EXT4 parsec', 'XFS parsec', 'OCFS2', 'unix parsec'],
    'orel_stand4':      ['postgresql-aud-off', 'postgresql', 'psql balance'],
    'smolensk_stand4':  ['postgresql-sm', 'psql parsec', 'psql vanilla'],
    'orel_stand10':     ['NTFS', 'psql kernels'],
    'smolensk_stand10': [],
    'orel_stand11':     ['docker-wa', 'FIO', 'steal time', 'vUnixBench', 'vPingPong'],
    'smolensk_stand11': ['parsec impact-fs', 'parsec impact-fs aud-off', 'steal time-sm', 'psql oom'],
    'orel_stand12':     ['syslog-ng'],
    'smolensk_stand12': ['auditd-f', 'auditd-p', 'auditd-u', 'digsig-cdt', 'apache-rp'],
    'orel_stand13':     ['syslog-ng-cwl'],
    'smolensk_stand13': []
}

from allta_image_conf import group_tests, stands_groups
tests = "['_stand11 group']"

for group in group_tests:
    print('[\'' + str(group) + '\']')
    if str(tests) == '[\'' + str(group) + '\']':
        print(str(sorted(stands_groups['_'.join(group.replace('_', '').split(' '))])))

from datetime import datetime, timedelta
import pandas as pd
import json


class TestTimeWatchdog:
    """
    Класс позволяет вести динамический подсчет времени, 
    затраченного на прогон с одним ядром.

    :param str upd_version: upd version (1.7 or 1.8 etc)
    :param str stand: номер стенда
    """
    def __init__(self,
                 upd_version=None,
                 stand=None):

        self.upd_version = upd_version
        self.stand = stand
        self.times_path = 'test_times.json'


    def transfer_test_time(self, test_name: str, time: str):
        """
        :param test_name: наименование теста в прогоне
        :param time: время, затраченное на выполнения теста
        """
        with open(self.times_path, 'r') as r:
            data = json.loads(r.read())
        
        if self.upd_version in data and self.stand in data[self.upd_version]: 
            data[self.upd_version][self.stand][test_name] = time

        with open(self.times_path, 'w') as w:
            json.dump(data, w, indent=4)


    def _counting_total_time(self):
        """
        Подсчет общего времени, затраченного на прогон с одним ядром
        """
        with open(self.times_path, 'r') as r:
            data = json.loads(r.read())

        def parse_time(time_str):
            if 'days' in time_str:
                days, time_str = time_str.split(' days, ')
                days = int(days)
            elif 'day' in time_str:
                days, time_str = time_str.split(' day, ')
                days = int(days)
            else:
                days = 0

            t = datetime.strptime(time_str.strip(), "%H:%M:%S")
            return timedelta(days=days, hours=t.hour, minutes=t.minute, seconds=t.second)

        def sum_times(times):
            total = timedelta()
            for time_str in times:
                total += parse_time(time_str)
            return total

        def format_time(total):
            days = total.days
            hours, remainder = divmod(total.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            if days > 1:
                return f"{days} days {hours}:{minutes:02}:{seconds:02}"
            elif days > 0:
                return f"{days} day {hours}:{minutes:02}:{seconds:02}"
            else:
                return f"{hours}:{minutes:02}:{seconds:02}"

        for version, stands in data.items():
            for stand, tests in stands.items():
                times = [time for test, time in tests.items() if test != "Total time"]
                total_time = sum_times(times)
                data[version][stand]["Total time"] = format_time(total_time)

        rows = {}
        for version, stands in data.items():
            for stand, tests in stands.items():
                for test, time in tests.items():
                    if test not in rows:
                        rows[test] = {}
                    rows[test][(version, stand)] = time

        return rows


    def create_html(self):
        """
        Создает html на основе полученных данных
        """

        current_day = datetime.now().date()
        html_head = f"""
        <br />
        <br />
        <h1>Время выполнения прогона для одного ядра. Актуально на {current_day}</h1>
        """

        df = pd.DataFrame(self._counting_total_time()).transpose()
        df = df.reindex(columns=sorted(df.columns, key=lambda x: (x[0], x[1])))
        

        total_time_row = df.loc['Total time']
        df = df.drop('Total time')
        df = pd.concat([df, total_time_row.to_frame().T])
        df.fillna('', inplace=True)

        html_result = df.to_html()
        html_page = '\n'.join([html_head + html_result])

        print(html_page)
        with open('templates/times.html', 'w') as w:
            w.write(html_page)

ttw = TestTimeWatchdog()
ttw.create_html()
