from setuptools import setup, find_packages

setup(
    name="allta_cli",
    version="1.0.1",
    packages=find_packages(),
    install_requires=["click", "requests", "allta"],
    entry_points={
        "console_scripts": [
            "allta_cli = allta_cli.__main__:main",  # точка входа
        ]
    },
)