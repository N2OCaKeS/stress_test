# from setuptools import setup, find_packages

# setup(
#     name="allta-vm",
#     version="1.0.0",
#     packages=find_packages(),
#     install_requires=["click", "allta"],
# )


from setuptools import setup, find_packages

setup(
    name="allta_vm",
    version="1.0.0",
    packages=find_packages(),
    install_requires=["click", "allta", "requests"],
    entry_points={
        "console_scripts": [
            "allta_vm = allta_vm.__main__:main",  # точка входа
        ]
    },
)