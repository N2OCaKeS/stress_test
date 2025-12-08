
# create venv in script_dir
PACKAGES=(
  build-essential
  zlib1g-dev
  libncurses5-dev
  libgdbm-dev
  libnss3-dev
  libssl-dev
  libreadline-dev
  libffi-dev
  libsqlite3-dev
  libbz2-dev
  python3-venv
)

sudo apt-get update
for pkg in "${PACKAGES[@]}"; do
  echo "Устанавливаем пакет ${pkg}..."
  sudo apt-get install -y "$pkg" || { echo "Ошибка установки пакета ${pkg}. Завершение."; exit 1; }
done


# install python 3.12
tar -xf srv/modules/Python-3.12.1.tar.xz
cd Python-3.12.1
./configure --enable-optimizations
make -j 6
sudo make altinstall

python3.12 -m venv venv
source venv/bin/activate


# check venv
VENV_PYTHON_VERSION=$(python3.12 --version)
if [[ "$VENV_PYTHON_VERSION" =~ ^Python\ 3\.12 ]]; then
    echo "✅ Python 3.12 доступен в виртуальном окружении."
else
    echo "❌ Ошибка: Python 3.12 не найден в виртуальном окружении!"
    deactivate
    exit 1
fi
