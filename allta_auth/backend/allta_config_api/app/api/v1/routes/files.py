from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse

from app.api.v1.dependencies import AuthVerifyResponse, get_current_admin_user, get_current_user
from app.utils.config import settings

router = APIRouter()

INFO_PATH = os.path.join(settings.DATA_DIR, "info.json")


@router.get(
    "/files/{filename}",
    summary="Download a file (auth required)",
)
def get_file(
    filename: str = Path(..., description="Name of file to download"),
    _user: AuthVerifyResponse = Depends(get_current_user),
):
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
            "info": records,
        },
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
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    dst_path = os.path.join(settings.DATA_DIR, file.filename)
    records: list[dict[str, Any]] = []

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
            detail=f"File '{file.filename}' already exists",
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
        "info": rec,
    }


@router.delete(
    "/files/{filename}",
    summary="Delete a file (admin only)",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_file(
    filename: str = Path(..., description="Name of file to delete"),
    _admin: AuthVerifyResponse = Depends(get_current_admin_user),
):
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

