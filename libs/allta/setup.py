from setuptools import setup, find_packages

def readme(): # TODO ПЕРЕПИСАТЬ README FILE
  with open('README.md', 'r') as f:
    return f.read()

setup(
  name='allta',
  version='0.0.1', # TODO Здесь МЕНЯТЬ версию при релизе на ту что в коммите
  author='team13',
  author_email='mfilippenko@astralinux.ru',
  description='Библиотеки для stress тестов',
  long_description=readme(),
  long_description_content_type='text/markdown',
  url='https://git.astralinux.ru/projects/QA/repos/stress_test/browse?at=refs%2Fheads%2Flibs',
  packages=find_packages(),
  install_requires=['paramiko', 'requests'],
  classifiers=[],
  keywords='astralinux stresstest vm',
  project_urls={
    'Documentation': 'https://git.astralinux.ru/projects/QA/repos/stress_test/browse?at=refs%2Fheads%2Flibs'
  },
  python_requires='>=3.7'
)