#!/bin/bash
# TODO подумать про сборку, возможно есть смысл перейти на pyinstyaller и собирать в бинарник
# TODO Пересобрать пакет allta-cli
set -e

BASE_PATH="$(pwd)"
PROJECT_NAME="$1"

if [ -z "$PROJECT_NAME" ]; then
    echo "❌ Укажите имя проекта, например: ./build.sh allta_vm"
    exit 1
fi

PROJECT_PATH="$BASE_PATH/$PROJECT_NAME"
PYTHON_VERSION="3.12.9"
PYTHON_BOOTSTRAP="$PROJECT_PATH/bootstrap"
PYTHON_PREFIX="$PYTHON_BOOTSTRAP/python"
PYTHON_BIN="$PYTHON_PREFIX/bin/python3"

install_deps() {
    echo "📥 Установка зависимостей сборки..."
    sudo apt-get update
    sudo apt-get install -y \
        build-essential libssl-dev zlib1g-dev \
        libncurses5-dev libncursesw5-dev libreadline-dev \
        libsqlite3-dev libffi-dev wget xz-utils tk-dev \
        libbz2-dev devscripts debhelper dh-python fakeroot \
        python3-all
}

install_python() {
    echo "🐍 Сборка Python $PYTHON_VERSION в $PYTHON_PREFIX"

    mkdir -p "$PYTHON_BOOTSTRAP"
    cd "$PYTHON_BOOTSTRAP"

    if [ ! -f "$PYTHON_BIN" ]; then
        wget -q "https://www.python.org/ftp/python/$PYTHON_VERSION/Python-$PYTHON_VERSION.tgz"
        tar xf "Python-$PYTHON_VERSION.tgz"
        cd "Python-$PYTHON_VERSION"

        ./configure --prefix="$PYTHON_PREFIX" --enable-optimizations --with-ensurepip=install
        make -j"$(nproc)"
        make install
    else
        echo "✅ Python уже собран"
    fi

    cd "$PYTHON_BOOTSTRAP"
    rm -rf "Python-$PYTHON_VERSION.tgz" "Python-$PYTHON_VERSION"

    echo "📦 Установка зависимостей Python-проекта..."
    cd "$PROJECT_PATH"
    "$PYTHON_BIN" -m pip install -U pip wheel
    if [ -f requirements.txt ]; then
        echo "📥 Установка зависимостей из requirements.txt..."
        "$PYTHON_BIN" -m pip install -r requirements.txt
    else
        echo "⚠️ Файл requirements.txt не найден, пропускаем установку зависимостей"
    fi

    echo "📦 Установка самого проекта..."
    "$PYTHON_BIN" -m pip install .
}

build_package() {
    echo "📦 Сборка .deb для $PROJECT_NAME"
    cd "$PROJECT_PATH"

    # Сохраняем список .deb до сборки
    BEFORE_DEB=$(mktemp)
    ls -1 "$BASE_PATH"/*.deb 2>/dev/null || true > "$BEFORE_DEB"

    debuild -us -uc

    # Сохраняем список .deb после сборки
    AFTER_DEB=$(mktemp)
    ls -1 "$BASE_PATH"/*.deb > "$AFTER_DEB"

    # Определяем новый .deb
    NEW_DEB=$(comm -13 <(sort "$BEFORE_DEB") <(sort "$AFTER_DEB") | grep -v "dbgsym" || true)

    if [ -n "$NEW_DEB" ]; then
        echo "✅ Сборка завершена. Новый пакет:"
        echo "$NEW_DEB"
    else
        echo "⚠️ Новый .deb не найден"
    fi

    rm -f "$BEFORE_DEB" "$AFTER_DEB"
}

cleanup() {
    echo "🧹 Очистка временных файлов"
    cd $BASE_PATH
    rm -rf *dbgsym*.deb *tar.gz *.changes *.build *.buildinfo *.dsc
    rm -rf ${PROJECT_NAME}/.pybuild ${PROJECT_NAME}/${PROJECT_NAME}.egg-info
    rm -rf ${PROJECT_NAME}/bootstrap/python/*
    rm -rf ${PROJECT_NAME}/debian/.debhelper \
           ${PROJECT_NAME}/debian/*.substvars \
           ${PROJECT_NAME}/debian/files \
           ${PROJECT_NAME}/debian/debhelper-build-stamp \
           ${PROJECT_NAME}/debian/allta* \
           ${PROJECT_NAME}/debian/debhelper.log
}

main() {
    if [ ! -d "$PROJECT_PATH" ]; then
        echo "❌ Проект не найден по пути: $PROJECT_PATH"
        exit 1
    fi

    install_deps
    install_python
    build_package
    sleep 10
    cleanup
}

main "$@"
