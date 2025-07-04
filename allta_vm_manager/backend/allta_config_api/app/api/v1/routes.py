import os
import json
from fastapi import (
    APIRouter, Depends, File, UploadFile, Form,
    HTTPException, status, Path
)
from fastapi.responses import FileResponse, JSONResponse
from app.api.v1.dependencies import get_current_user, get_current_admin_user
from app.api.v1.dependencies import AuthVerifyResponse

router = APIRouter()

DATA_DIR = "/data"
INFO_PATH = os.path.join(DATA_DIR, "info.json")

@router.get(
    "/{filename}",
    summary="Download a file (public)",
)
def get_file(
    filename: str = Path(..., description="Name of file to download"),
):
    """
    Отдаёт файл по имени.
    Если его нет — возвращает JSON с ошибкой и содержимым info.json.
    """
    safe_name = os.path.basename(filename)
    file_path = os.path.join(DATA_DIR, safe_name)

    # Если файл есть - отдаём файл
    if os.path.isfile(file_path):
        return FileResponse(file_path, filename=safe_name)

    # Если файла нет - читаем info.json
    records = []
    if os.path.exists(INFO_PATH):
        with open(INFO_PATH, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    records = data
            except json.JSONDecodeError:
                # Если файл пустой или повреждён
                records = []

    # Возвращаем JSON с detail и info.json
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={
            "detail": f"File '{filename}' not found",
            "info": records
        }
    )


@router.post(
    "/upload/",
    summary="Upload a file (auth required)",
    status_code=status.HTTP_201_CREATED,
)
async def upload_file(
    file: UploadFile = File(...),
    description: str = Form(...),
    user: AuthVerifyResponse = Depends(get_current_user),
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

    # Загружаем существующие записи
    if os.path.exists(INFO_PATH):
        with open(INFO_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                records = data

    exists = os.path.isfile(dst_path)
    if exists and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"File '{file.filename}' already exists"
        )

    # Сохраняем файл
    content = await file.read()
    with open(dst_path, "wb") as f:
        f.write(content)

    # Обновляем info.json
    updated = False
    for rec in records:
        if rec.get("filename") == file.filename:
            rec["description"] = description
            rec["updated_by"] = user.login
            updated = True
            break

    if not updated:
        records.append({
            "filename": file.filename,
            "description": description,
            "uploaded_by": user.login,
        })

    with open(INFO_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    return {
        "detail": "File overwritten" if exists else "File uploaded",
        "info": records[-1] if not updated else rec
    }


@router.delete(
    "/{filename}",
    summary="Delete a file (admin only)",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_file(
    filename: str = Path(..., description="Name of file to delete"),
    admin: AuthVerifyResponse = Depends(get_current_admin_user),
):
    """
    Удаляет файл из DATA_DIR и запись в info.json.
    Только администратор.
    """
    file_path = os.path.join(DATA_DIR, filename)
    if os.path.isfile(file_path):
        os.remove(file_path)

    records = []
    if os.path.exists(INFO_PATH):
        with open(INFO_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                records = [r for r in data if r.get("filename") != filename]

    with open(INFO_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    return


