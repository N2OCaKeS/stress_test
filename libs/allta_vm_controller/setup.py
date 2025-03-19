from setuptools import setup, find_packages

def readme(): # TODO ПЕРЕПИСАТЬ README FILE
  with open('README.md', 'r') as f:
    return f.read()



setup(
  name='allta-vm-controller',
  version='0.0.1',
  author='mfilippenko',
  author_email='mfilippenko@astralinux.ru',
  description='Базовые декораторы',
  long_description=readme(),
  long_description_content_type='text/markdown',
  url='https://git.astralinux.ru/projects/QA/repos/stress_test/browse?at=refs%2Fheads%2Flibs',
  packages=find_packages(),
  install_requires=['paramiko', 'requests', 'astralinux_decorators'],
  classifiers=[],
  keywords='astralinux stresstest vm',
  project_urls={
    'Documentation': 'link'
  },
  python_requires='>=3.7'
)