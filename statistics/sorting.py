import numpy as np
import pandas as pd
from abc import abstractmethod
from distutils.version import LooseVersion

class Scale:
    @staticmethod
    def get_base_scale_text(dataframe):
        return dataframe['Релиз'] + "_" + dataframe['Ядро']


class Sort:
    @abstractmethod
    def sort(data):
        pass


class SortMainTable(Sort):
    @staticmethod
    def sort(dataframe) -> pd.DataFrame:
        dataframe['Sort'] = dataframe['Релиз'].apply(lambda s: [LooseVersion(x) for x in s.split('.', 1)])
        dataframe.sort_values(by='Sort', inplace=True)
        dataframe.drop(columns='Sort', inplace=True)
        dataframe.reset_index(drop=True, inplace=True)
        return dataframe


class SortUniqueMajorKernel(Sort):
    """
        Здесь должна быть реализации по удалению ненужных ядер из статистики
        Например: Есть 2 протокола испытания версии 1.7.5, первый с ядром 6.1.29-1-generic и второй с ядром 6.1.50-1-generic.
        Решение: Необходимо удалить строчки из DF(набора данных) с ядром 6.1.29-1-generic (Так как есть более новая версия ядра. Считать с 3 позиции: 29 < 50)
    """

    @staticmethod
    def groupby_uniq_kernel(uniq_kernels: list):
        grouped_kernel = []
        for item in uniq_kernels:
            first_part = ".".join(item.split(".")[:2])
            last_part = item.split("-")[-1]
            kernel = first_part + "-" + last_part
            if kernel not in grouped_kernel:
                grouped_kernel.append(kernel)
        return grouped_kernel
    
    @staticmethod
    def filter_by_kernel_version(df, kernel):
        kernel_major_vers, kernel_end_vers = kernel.split("-")
        kernel_major_vers = f"{kernel_major_vers}."
        return df[(df["Ядро"].str.startswith(kernel_major_vers)) & (df["Ядро"].str.endswith(kernel_end_vers))]
    
    @classmethod
    def sort(cls, old_dataframe) -> pd.DataFrame:
        unique_versions = list(old_dataframe['Релиз'].unique())
        unique_kernels = list(old_dataframe['Ядро'].unique())
        kernel_keys = cls.groupby_uniq_kernel(unique_kernels)
        new_df_temp = pd.DataFrame()
        for version in unique_versions:
            df_for_each_version = old_dataframe.loc[old_dataframe["Релиз"] == f"{version}"]
            for kernel in kernel_keys:
                df_with_one_kernel =  cls.filter_by_kernel_version(df=df_for_each_version, kernel=kernel)
                df_with_one_kernel = df_with_one_kernel.copy()
                try:
                    df_with_one_kernel[['numeric_version', 'additional_digits', 'kernel_type']] = df_with_one_kernel['Ядро'].str.split('-', expand=True)
                    df_with_one_kernel['additional_digits'] = df_with_one_kernel['additional_digits'].astype(int)
                except ValueError:
                    df_with_one_kernel[['numeric_version', 'additional_digits', 'kernel_type', 'minor_version']] = 0
                df_with_one_kernel['minor_version'] = df_with_one_kernel['numeric_version'].astype(str).str.split(".").str[-1].astype(int)
                max_minor_value = df_with_one_kernel['minor_version'].max()
                df_sort_by_minor_version = df_with_one_kernel[df_with_one_kernel['minor_version'] == max_minor_value]
                max_add_digit_value = df_sort_by_minor_version['additional_digits'].max()
                df_sort_by_minor_version_and_add_digit = df_sort_by_minor_version[df_sort_by_minor_version['additional_digits'] == max_add_digit_value]
                df_sort_by_minor_version_and_add_digit = df_sort_by_minor_version_and_add_digit.drop(['numeric_version', 'additional_digits', 'kernel_type', 'minor_version'], axis=1)
                new_df_temp = new_df_temp._append(df_sort_by_minor_version_and_add_digit)
            if "1.7" not in df_for_each_version['Релиз'].iloc[0] and "1.8" not in df_for_each_version['Релиз'].iloc[0]:
                new_df_temp = new_df_temp._append(df_for_each_version)
        return new_df_temp