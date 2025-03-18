from setuptools import setup, find_packages

def readme():
    with open('README.md', 'r', encoding='utf-8') as f:
        return f.read()

setup(
    name='astralinux-decorators',
    version='0.0.3',
    author='mfilippenko',
    author_email='mfilippenko@astralinux.ru',
    description='Базовые декораторы',
    long_description=readme(),
    long_description_content_type='text/markdown',
    url='https://git.astralinux.ru/projects/QA/repos/stress_test/browse?at=refs%2Fheads%2Flibs',
    packages=find_packages(),
    install_requires=[],
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Libraries',
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent'
    ],
    keywords='astralinux stresstest decorator',
    project_urls={
        'Documentation': 'https://git.astralinux.ru/projects/QA/repos/stress_test',  # замените на реальную ссылку
        'Source': 'https://git.astralinux.ru/projects/QA/repos/stress_test',
        'Tracker': 'https://git.astralinux.ru/projects/QA/issues'
    },
    python_requires='>=3.7',
    include_package_data=True
)
