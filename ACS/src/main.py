### uvicorn src.main:app --host 0.0.0.0  --reload

import sys
import os
from fastapi import FastAPI, Depends, HTTPException
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
from src.schemas import Stand, Version, Snapshot, Repo, CreateRepo
from sqlalchemy import select, insert
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_async_session
from src.models import versions, stands, repos

from src.clonezilla_snap.router import router as clonezilla_router
from src.add_tuning.router import router as add_tunning_router

# sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


app = FastAPI(
    title="ACS"
)

app.include_router(add_tunning_router)
app.include_router(clonezilla_router)

@app.get("/")
def index():
    return {"Hello world"}


@app.get("/stands/{stand_name}", response_model=Stand)
async def get_stand(stand_name: str, session: AsyncSession = Depends(get_async_session)):
    query = select(stands).where(stands.c.name == stand_name)
    result = await session.execute(query)
    return result.first()

@app.post("/stands")
async def create_stand(new_stand: Stand, session: AsyncSession = Depends(get_async_session)):
    stmt = insert(stands).values(**new_stand.dict())
    await session.execute(stmt)
    await session.commit()
    return {"status": "success"}

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
    stmt = insert(versions).values(**new_version.dict())
    await session.execute(stmt)
    await session.commit()
    return {"status": "success"}

@app.get("/repos/")
async def get_repo(session: AsyncSession = Depends(get_async_session)):
    query = select(repos)
    print(query)
    # j = join(repos, versions, repos.c.id == versions.c.id)
    # query = select([repos, versions]).select_from(j)
    result = await session.execute(query)
    return result.mappings().all()

@app.post("/repos")
async def add_repo(new_repo: CreateRepo, session: AsyncSession = Depends(get_async_session)):
    stmt = insert(repos).values(**new_repo.dict())
    await session.execute(stmt)
    await session.commit()
    return {"status": "success"}
# app.include_router(clonezilla_router)

async def get_snapshots():
    pass

async def add_snapshots():
    pass
