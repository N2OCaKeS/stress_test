from setuptools import setup, find_packages

setup(
    name="allta_cli",
    version="1.0.3",
    packages=find_packages(),
    install_requires=["click", "requests"],
    entry_points={
        "console_scripts": [
            "allta = allta_cli.__main__:main",
            "allta_cli = allta_cli.__main__:main",  # точка входа
        ]
    },
)
