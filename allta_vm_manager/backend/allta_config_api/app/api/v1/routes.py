import os
import json
from fastapi import (
    APIRouter, Depends, File, UploadFile, Form,
    HTTPException, status, Path
)
from fastapi.responses import FileResponse
from app.api.v1.dependencies import verify_with_auth_api
from typing import Dict

router = APIRouter()

DATA_DIR  = "/data"
INFO_PATH = os.path.join(DATA_DIR, "info.json")


@router.post(
    "/upload/",
    summary="Upload a file (auth required)",
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    file: UploadFile = File(...),
    description: str    = Form(...),
    user_info: Dict     = Depends(verify_with_auth_api),
):
    """
    Сохраняет файл в DATA_DIR.
    - Любой авторизованный может загрузить новый файл.
    - Перезапись существующего файла разрешена только администратору.
    При добавлении/обновлении меняется info.json:
    для новых файлов добавляется запись, для перезаписи обновляется description.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    dst_path = os.path.join(DATA_DIR, file.filename)
    records = []

    # Загрузим существующие записи
    if os.path.exists(INFO_PATH):
        with open(INFO_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                records = data

    # Проверим существование файла на диске
    exists = os.path.isfile(dst_path)
    if exists and not user_info.get("is_admin", False):
        # не админ не может перезаписывать
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"File '{file.filename}' already exists"
        )

    # Сохраняем (или перезаписываем) файл
    content = await file.read()
    with open(dst_path, "wb") as f:
        f.write(content)

    # Обновим info.json
    updated = False
    for rec in records:
        if rec.get("filename") == file.filename:
            rec["description"] = description
            rec["updated_by"]  = user_info.get("login")
            updated = True
            break

    if not updated:
        records.append({
            "filename":    file.filename,
            "description": description,
            "uploaded_by": user_info.get("login"),
        })

    # Запишем обратно
    with open(INFO_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    return {
        "detail": (
            "File overwritten" if exists else "File uploaded"
        ),
        "info": records[-1] if not updated else rec
    }


@router.delete(
    "/{filename}",
    summary="Delete a file (admin only)",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_file(
    filename: str = Path(..., description="Name of file to delete"),
    user_info: Dict = Depends(verify_with_auth_api),
):
    """
    Удаляет файл из DATA_DIR и соответствующую запись в info.json.
    Только администратор.
    """
    if not user_info.get("is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough privileges"
        )

    file_path = os.path.join(DATA_DIR, filename)
    if os.path.isfile(file_path):
        os.remove(file_path)
    # else — ничего не удалять, но очистим запись ниже

    # Обновляем info.json, убирая записи с этим именем
    records = []
    if os.path.exists(INFO_PATH):
        with open(INFO_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                records = [r for r in data if r.get("filename") != filename]

    with open(INFO_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    # 204 — без тела
    return


@router.get(
    "/{filename}",
    summary="Download a file (public)",
)
def get_file(
    filename: str = Path(..., description="Name of file to download"),
):
    """
    Отдаёт файл по имени. Если его нет — отдаёт info.json.
    """
    # Защита от ../../
    safe_name = os.path.basename(filename)
    file_path = os.path.join(DATA_DIR, safe_name)

    if os.path.isfile(file_path):
        return FileResponse(file_path, filename=safe_name)

    # Если не нашли — вернём info.json
    return FileResponse(
        INFO_PATH,
        filename="info.json",
        media_type="application/json"
    )