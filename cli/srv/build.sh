#!/bin/bash
# TODO подумать про сборку, возможно есть смысл перейти на pyinstyaller и собирать в бинарник
# TODO Пересобрать пакет allta-cli
set -euo pipefail

BASE_PATH="$(pwd)"
PROJECT_NAME="${1:-}"

if [ -z "$PROJECT_NAME" ]; then
    echo "❌ Укажите имя проекта, например: ./build.sh allta_vm"
    exit 1
fi

PROJECT_PATH="$BASE_PATH/$PROJECT_NAME"
PYTHON_BOOTSTRAP="$PROJECT_PATH/bootstrap"
PYTHON_PREFIX="$PYTHON_BOOTSTRAP/python"
PYTHON_BIN="$PYTHON_PREFIX/bin/python3.12"
PREBUILT_PYTHON_ROOT="${ALLTA_BUILDER_PYTHON:-/opt/allta-builder/python}"

prepare_python() {
    if [ -x "$PYTHON_BIN" ]; then
        echo "✅ Bootstrap Python уже подготовлен: $PYTHON_PREFIX"
        return 0
    fi

    if [ ! -x "$PREBUILT_PYTHON_ROOT/bin/python3.12" ]; then
        echo "❌ Готовый Python не найден по пути: $PREBUILT_PYTHON_ROOT" >&2
        echo "Ожидается базовый образ с предустановленным Python." >&2
        exit 1
    fi

    echo "🐍 Копирую готовый Python из образа: $PREBUILT_PYTHON_ROOT -> $PYTHON_PREFIX"
    mkdir -p "$PYTHON_BOOTSTRAP"
    rm -rf "$PYTHON_PREFIX"
    cp -a "$PREBUILT_PYTHON_ROOT" "$PYTHON_PREFIX"
}

prepare_embedded_runtime() {
    if [ "$PROJECT_NAME" != "allta_cli" ]; then
        return 0
    fi

    echo "🔧 Подготавливаю embedded runtime (allta + allta_cli)"
    "$PYTHON_BIN" -m pip install --upgrade pip wheel setuptools
    "$PYTHON_BIN" -m pip uninstall -y numpy pandas scikit-learn matplotlib || true
    "$PYTHON_BIN" -m pip install --upgrade -r "$PROJECT_PATH/requirements.txt"
    "$PYTHON_BIN" -m pip install --no-deps --upgrade allta -i http://10.177.103.10:3141/root/release --trust 10.177.103.10
    "$PYTHON_BIN" -m pip install --no-deps --upgrade --force-reinstall "$PROJECT_PATH"

    "$PYTHON_BIN" - <<'PY'
import importlib
import importlib.util

for mod in ("allta_cli.__main__",):
    importlib.import_module(mod)

if importlib.util.find_spec("allta") is None:
    raise RuntimeError("allta is not installed in embedded runtime")
if importlib.util.find_spec("numpy") is not None:
    raise RuntimeError("numpy should not be installed in embedded runtime")

print("✅ Embedded runtime готов")
PY
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
    cd "$BASE_PATH"
    rm -rf *dbgsym*.deb *tar.gz *.changes *.build *.buildinfo *.dsc
    rm -rf "${PROJECT_NAME}/.pybuild" "${PROJECT_NAME}/${PROJECT_NAME}.egg-info"
    rm -rf "${PROJECT_NAME}/bootstrap/python"
    rm -rf "${PROJECT_NAME}/debian/.debhelper" \
           "${PROJECT_NAME}/debian/"*.substvars \
           "${PROJECT_NAME}/debian/files" \
           "${PROJECT_NAME}/debian/debhelper-build-stamp" \
           "${PROJECT_NAME}/debian/allta"* \
           "${PROJECT_NAME}/debian/debhelper.log"
}

main() {
    if [ ! -d "$PROJECT_PATH" ]; then
        echo "❌ Проект не найден по пути: $PROJECT_PATH"
        exit 1
    fi

    prepare_python
    prepare_embedded_runtime
    build_package
    cleanup
}

main "$@"
