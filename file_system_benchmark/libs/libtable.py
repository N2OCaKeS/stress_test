import pandas

from fsb_conf import REPORT_FILENAME

class Report:
    def __init__(self, report='../report/{}'.format(REPORT_FILENAME)):

        '''
            :param report: path to report file
            read and parsing data from report file
        '''
        with open(report, 'r') as report_file:
            raw_data = report_file.read().split()
            self.fs_use_lst = [int(param) for param in raw_data[0::23]]  # percents
            self.file_count_list = [int(param) for param in raw_data[1::23]]
            self.file_size_list = [int(param) for param in raw_data[2::23]]
            self.speed_list = [float(param) for param in raw_data[3::23]]
            self.app_overhead_list = [int(param) for param in raw_data[4::23]]
            self.create_min_list = [int(param) for param in raw_data[5::23]]
            self.create_avg_list = [int(param) for param in raw_data[6::23]]
            self.create_max_list = [int(param) for param in raw_data[7::23]]
            self.write_min_list = [int(param) for param in raw_data[8::23]]
            self.write_avg_list = [int(param) for param in raw_data[9::23]]
            self.write_max_list = [int(param) for param in raw_data[10::23]]
            self.fsync_min_list = [int(param) for param in raw_data[11::23]]
            self.fsync_avg_list = [int(param) for param in raw_data[12::23]]
            self.fsync_max_list = [int(param) for param in raw_data[13::23]]
            self.sync_min_list = [int(param) for param in raw_data[14::23]]
            self.sync_avg_list = [int(param) for param in raw_data[15::23]]
            self.sync_max_list = [int(param) for param in raw_data[16::23]]
            self.close_min_list = [int(param) for param in raw_data[17::23]]
            self.close_avg_list = [int(param) for param in raw_data[18::23]]
            self.close_max_list = [int(param) for param in raw_data[19::23]]
            self.unlink_min_list = [int(param) for param in raw_data[20::23]]
            self.unlink_avg_list = [int(param) for param in raw_data[21::23]]
            self.unlink_max_list = [int(param) for param in raw_data[22::23]]
            self.raw_table = pandas.DataFrame({ 'fs_use': self.fs_use_list,
                                                'file_count': self.file_count_list,
                                                'file_size': self.file_size_list,
                                                'speed': self.speed_list,
                                                'app_overhead': self.app_overhead_list,
                                                'create_min': self.create_min_list,
                                                'create_avg': self.create_avg_list,
                                                'create_max': self.create_max_list,
                                                'write_min': self.write_min_list,
                                                'write_avg': self.write_avg_list,
                                                'write_max': self.write_max_list,
                                                'fsync_min': self.fsync_min_list,
                                                'fsync_avg': self.fsync_avg_list,
                                                'fsync_max': self.fsync_max_list,
                                                'sync_min': self.sync_min_list,
                                                'sync_avg': self.sync_avg_list,
                                                'sync_max': self.sync_max_list,
                                                'close_min': self.close_min_list,
                                                'close_avg': self.close_avg_list,
                                                'close_max': self.close_max_list,
                                                'unlink_min': self.unlink_min_list,
                                                'unlink_avg': self.unlink_avg_list,
                                                'unlink_max': self.unlink_max_list})


r = Report()