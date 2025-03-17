from setuptools import setup, find_packages

def readme():
  with open('README.md', 'r') as f:
    return f.read()

# setup(
#     name='astralinux-vm-controller', # имя вашего пакета
#     version='0.0.1',                 # версия
#     packages=find_packages(),        # автоматически находит пакеты в директории
#     description='Модуль для работы с ВМ в среде astralinux',
#     author='mfilippenko',
#     author_email='mfilippenko@astralinux.ru',
#     url='https://git.astralinux.ru/projects/QA/repos/stress_test/browse?at=refs%2Fheads%2Flibs',
#     install_requires=[],             # зависимости, если есть
# )


setup(
  name='astralinux-decorators',
  version='0.0.1',
  author='mfilippenko',
  author_email='mfilippenko@astralinux.ru',
  description='Базовые декораторы',
  long_description=readme(),
  long_description_content_type='text/markdown',
  url='https://git.astralinux.ru/projects/QA/repos/stress_test/browse?at=refs%2Fheads%2Flibs',
  packages=find_packages(),
  install_requires=[],
  classifiers=[],
  keywords='astralinux stresstest decorator',
  project_urls={
    'Documentation': 'link'
  },
  python_requires='>=3.7'
)