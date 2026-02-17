import os
import json
from fastapi import (
    APIRouter, Depends, File, UploadFile, Form,
    HTTPException, status, Path
)
from fastapi.responses import FileResponse, JSONResponse
from app.api.v1.dependencies import (
    get_current_user,
    get_current_admin_user,
    require_permission,
)
from app.api.v1.dependencies import AuthVerifyResponse
from app.utils.config import settings
router = APIRouter()


INFO_PATH = os.path.join(settings.DATA_DIR, "info.json")


# ---- ЗАЩИЩЁННАЯ выдача файла ----
@router.get(
    "/files/{filename}",
    summary="Download a file (auth required)",
)
def get_file(
    filename: str = Path(..., description="Name of file to download"),
    user: AuthVerifyResponse = Depends(get_current_user),  # <-- защита
):
    """
    Отдаёт файл по имени (только для авторизованных).
    Если его нет — возвращает JSON с ошибкой и содержимым info.json.
    """
    safe_name = os.path.basename(filename)
    file_path = os.path.join(settings.DATA_DIR, safe_name)

    if os.path.isfile(file_path):
        return FileResponse(file_path, filename=safe_name)

    records = []
    if os.path.exists(INFO_PATH):
        with open(INFO_PATH, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    records = data
            except json.JSONDecodeError:
                records = []

    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={
            "detail": f"File '{filename}' not found",
            "info": records
        }
    )


@router.post(
    "/files/upload/",
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
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    dst_path = os.path.join(settings.DATA_DIR, file.filename)
    records = []

    if os.path.exists(INFO_PATH):
        with open(INFO_PATH, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    records = data
            except json.JSONDecodeError:
                records = []

    exists = os.path.isfile(dst_path)
    if exists and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"File '{file.filename}' already exists"
        )

    content = await file.read()
    with open(dst_path, "wb") as f:
        f.write(content)

    updated = False
    rec = None
    for r in records:
        if r.get("filename") == file.filename:
            r["description"] = description
            r["updated_by"] = user.login
            updated = True
            rec = r
            break

    if not updated:
        rec = {
            "filename": file.filename,
            "description": description,
            "uploaded_by": user.login,
        }
        records.append(rec)

    with open(INFO_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    return {
        "detail": "File overwritten" if exists else "File uploaded",
        "info": rec
    }


@router.delete(
    "/files/{filename}",
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
    file_path = os.path.join(settings.DATA_DIR, os.path.basename(filename))
    if os.path.isfile(file_path):
        os.remove(file_path)

    records = []
    if os.path.exists(INFO_PATH):
        with open(INFO_PATH, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if isinstance(data, list):
                    records = [r for r in data if r.get("filename") != filename]
            except json.JSONDecodeError:
                records = []

    with open(INFO_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    return


# ---- НОВЫЙ эндпоинт: получить токены (авторизованные) ----
@router.get(
    "/tokens",
    summary="Get tokens file content (requires permission)",
)
def get_tokens(
    user: AuthVerifyResponse = Depends(require_permission(settings.TOKENS_READ_PERMISSION)),
):
    """
    Возвращает содержимое файла tokens.json только при наличии права.
    """
    if not os.path.exists(settings.TOKENS_PATH):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tokens file not found",
        )

    try:
        with open(settings.TOKENS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tokens file is corrupted (invalid JSON)",
        )
    allowed_keys = {
        "username", "conf_token", "ba",
        "jira_token", "git_token", "pass", "srv_pass"
    }
    result = {k: raw.get(k, "") for k in allowed_keys}

    return result
