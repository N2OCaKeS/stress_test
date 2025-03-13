from setuptools import setup, find_packages

setup(
    name='astralinux-vm-controller', # имя вашего пакета
    version='0.0.1',                 # версия
    packages=find_packages(),        # автоматически находит пакеты в директории
    description='Модуль для работы с ВМ в среде astralinux',
    author='mfilippenko',
    author_email='mfilippenko@astralinux.ru',
    url='https://git.astralinux.ru/projects/QA/repos/stress_test/browse?at=refs%2Fheads%2Flibs',
    install_requires=[],             # зависимости, если есть
)
