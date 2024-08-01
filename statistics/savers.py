import pandas as pd

from utils import UtilForBuildPath

class ConvertTableToHtml:
    @staticmethod
    def convert(dataframe: pd.DataFrame) -> str:
        table = dataframe.to_html(escape=False, index=False)
        return table


class SaveTableToFile:
    def __init__(self, main_folder, stat_rc_vers):
        self.main_folder = main_folder
        self.stat_rc_vers = stat_rc_vers

    def save(self, dataframe: pd.DataFrame, name: str, desc: str):
        table_html = ConvertTableToHtml.convert(dataframe=dataframe)
        file = open(f'{UtilForBuildPath.build_path(self.main_folder, stat_rc_vers=self.stat_rc_vers)}/{name}', "w")
        file.writelines(desc + table_html)
        file.close()


class SaveGraph:
    def __init__(self, main_folder, stat_rc_vers):
        self.main_folder = main_folder
        self.stat_rc_vers = stat_rc_vers

    def save(self, plot, name):
        plot.savefig(f"{UtilForBuildPath.build_path(self.main_folder, stat_rc_vers=self.stat_rc_vers)}/{name}.png")