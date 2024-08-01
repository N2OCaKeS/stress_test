import os

class UtilForBuildPath:
    @staticmethod
    def build_path(main_folder, stat_rc_vers=None):
        if stat_rc_vers:
            folder = "statistics_rc"
            if not stat_rc_vers in os.listdir(f"{main_folder}/{folder}"):
                os.mkdir(f"{main_folder}/{folder}/{stat_rc_vers}")
            folder = f"{folder}/{stat_rc_vers}"
        else:
            folder = "statistics"
        path = f"{main_folder}/{folder}"
        # if not folder in os.listdir(main_folder):
        #     os.mkdir(path)
        return path