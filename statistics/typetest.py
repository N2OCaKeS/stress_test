

class TypeTest:
    type_test_dict = {
        "unix": "UnixBench",
        "unix_parsec": "UnixBench parsec",
        "auditd-p": "Auditd-procces",
        "auditd-u": "Auditd-user",
        "auditd-f": "Auditd-files",
        "syslog-ng": "Syslog-NG",
    }
    @staticmethod
    def get_type_test(dataframe):
        return dataframe['type_test'].values[0]
    
    @classmethod
    def get_full_name_test(cls, dataframe):
        try:
            return cls.type_test_dict[dataframe['type_test'].values[0]]
        except KeyError:
            return dataframe['type_test'].values[0]

    @classmethod    
    def get_full_name_test_without_df(cls, key):
        try:
            return cls.type_test_dict[key]
        except KeyError:
            return key