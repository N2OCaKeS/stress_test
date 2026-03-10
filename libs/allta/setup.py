from setuptools import setup, find_packages

def readme(): 
  with open('README.md', 'r') as f:
    return f.read()

setup(
  name='allta',
  version='1.1.7', 
  author='team13',
  author_email='mfilippenko@astralinux.ru',
  description='Библиотеки для stress тестов',
  long_description=readme(),
  long_description_content_type='text/markdown',
  url='https://git.astralinux.ru/projects/QA/repos/stress_test/browse?at=refs%2Fheads%2Flibs',
  packages=find_packages(),
  install_requires=['paramiko', 'requests', 'pytz', 'atlassian-python-api', 'scikit-learn', 'numpy', 'pandas==2.2.3', 'matplotlib'],
  classifiers=[],
  keywords='astralinux stresstest vm',
  python_requires='>=3.11',
  package_data={
    "allta": ["**/*.sh"],
  },
)
