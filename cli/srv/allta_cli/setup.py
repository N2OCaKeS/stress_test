import re
from pathlib import Path

from setuptools import setup, find_packages


def _read_version():
    init_file = Path(__file__).parent / "allta_cli" / "__init__.py"
    text = init_file.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise RuntimeError("Не найден __version__ в allta_cli/__init__.py")
    return match.group(1)


setup(
    name="allta_cli",
    version=_read_version(),
    packages=find_packages(),
    install_requires=["click", "requests", "devpi-client", "packaging", "pyproject_hooks"],
    entry_points={
        "console_scripts": [
            "allta = allta_cli.__main__:main",
            "allta_cli = allta_cli.__main__:main",  # точка входа
        ]
    },
)
