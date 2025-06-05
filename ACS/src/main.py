### uvicorn src.main:app --host 0.0.0.0  --reload

import sys
import os
from celery import chain
from celery.result import AsyncResult
from fastapi import FastAPI, Depends, HTTPException
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, insert, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.schemas import Stand, Version, Snapshot, Repo
from src.database import get_async_session
from src.models import versions, stands, repos

from src.clonezilla_snap.clonezilla_func import backup_image, get_snapshot, debug_task
from src.clonezilla_snap.router import router as clonezilla_router
from src.add_tuning.router import router as add_tunning_router
from src.add_tuning.router import get_info_stand
from src.add_tuning.temp import astra_version_update
from src.utils.secondary_func import func_filter_version

# sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


app = FastAPI(
    title="ACS"
)

app.include_router(add_tunning_router)
app.include_router(clonezilla_router)

"""
    TODO нужна функция, которая вернет все стенды
"""

@app.get("/")
def index():
    return {"Hello world"}


@app.get("/stands/{stand_name}", response_model=Stand)
async def get_stand(stand_name: str, session: AsyncSession = Depends(get_async_session)):
    try:
        query = select(stands).where(stands.c.name == stand_name)
        result = await session.execute(query)
        return result.first()
    except Exception as e:
        return {"status": "failed", "error": e}
    

@app.get("/stands/all_stands")
async def get_all_stands(only_name: bool = False, session: AsyncSession = Depends(get_async_session)):
    try:
        if only_name:
            query = select(stands.c.name)
        else:
            query = select(stands)
        result = await session.execute(query)
        return result
    except Exception as e:
        return {"status": "failed", "error": e}


@app.post("/stands")
async def create_stand(new_stand: Stand, session: AsyncSession = Depends(get_async_session)):
    print("testing!@!!!!!")
    stmt = insert(stands).values(**new_stand.dict(exclude_none=True))
    print(stmt)
    await session.execute(stmt)
    await session.commit()
    return {"status": "success"}


@app.delete("/stands")
async def delete_stand(stand_name: str, session: AsyncSession = Depends(get_async_session)):
    stmt = delete(stands).where(stands.c.name == stand_name)
    result = await session.execute(stmt)
    await session.commit()
    return {"status": "success"} if result.rowcount else {"status": "not found"}

@app.get('/versions')
async def get_versions(session: AsyncSession = Depends(get_async_session)):
    try:
        query = select(versions)
        result = await session.execute(query)
        # print(result.scalars().all())
        return {
            "status": "success",
            "data": result.mappings().all(),
            "details": None
        }
    except Exception:
        # Передать ошибку разработчикам
        raise HTTPException(status_code=500, detail={
            "status": "error",
            "data": None,
            "details": None
        })

@app.post('/versions')
async def add_versions(new_version: Version, session: AsyncSession = Depends(get_async_session)):
    stmt = insert(versions).values(**new_version.dict(exclude_none=True)).returning(versions.c.id)
    result = await session.execute(stmt)
    new_id = result.scalar_one()
    await session.commit()
    return {"status": "success", "data": new_id}

@app.delete('/versions')
async def delete_versions(version_id: int, session: AsyncSession = Depends(get_async_session)):
    stmt = delete(versions).where(versions.c.id == version_id)
    result = await session.execute(stmt)
    await session.commit()
    return {"status": "success"} if result.rowcount else {"status": "not found"}


@app.get("/repos/")
async def get_repo(session: AsyncSession = Depends(get_async_session)):
    query = select(repos)
    print(query)
    # j = join(repos, versions, repos.c.id == versions.c.id)
    # query = select([repos, versions]).select_from(j)
    result = await session.execute(query)
    return result.mappings().all()

@app.post("/repos")
async def add_repo(new_repo: Repo, session: AsyncSession = Depends(get_async_session)):
    stmt = insert(repos).values(**new_repo.dict(exclude_none=True))
    await session.execute(stmt)
    await session.commit()
    return {"status": "success"}

@app.delete('/repos')
async def delete_repos(id_repo: int, session: AsyncSession = Depends(get_async_session)):
    stmt = delete(repos).where(repos.c.id == id_repo)
    result = await session.execute(stmt)
    await session.commit()
    return {"status": "success"} if result.rowcount else {"status": "not found"}


@app.get("/result_full_snap/{task_id}")
def check_status_task(task_id):
    task_result = AsyncResult(task_id)
    if not task_result.ready():
        return {"status": "Выполняется"}
    else:
        return {"status": "Снимок готов"}


@app.post("/create_full_snap")
def create_full_snap(restore_version: str, version_to_update: str, password_cs: str, stand = Depends(get_info_stand)):
    restore_version_for_cs = func_filter_version(restore_version)
    snap_name_restore = stand[1] + "-" + restore_version_for_cs

    new_version_for_cs = func_filter_version(version_to_update)
    snap_name_backup = stand[1] + "-" + new_version_for_cs

    chain_task = chain(backup_image.si(stand=list(stand), snap_name=snap_name_restore, password_cs=password_cs, restore=True),
                       astra_version_update.si(new_version=version_to_update, stand=list(stand)),
                       backup_image.si(stand=list(stand), snap_name=snap_name_backup, password_cs=password_cs, restore=False),
                       get_snapshot.si(password_clonezilla_server=password_cs, snap_name=snap_name_backup)
                       )
    result = chain_task.apply_async()
    # status = result.status # или result.state
    
    # return {"chain_task_id": result.id, "status": result.status, "state": result.state}
    return {"status": "success"}


@app.get("/check_task/id")
def check_task(task_id: str):
    from src.tasks.tasks import celery
    task_result = celery.AsyncResult(task_id)
    return {"res": task_result.state}

@app.get("/test_debug_task")
def test_debug_task():
    # res = debug_task.apply_async()
    chain_task = chain(debug_task.si(), debug_task.si())
    
    result = chain_task.apply_async()

    task_ids = [task.id for task in result.children] if result.children else []

    return {"id": result.id, "state": result.state, "status": result.status, "task_ids": task_ids}
    