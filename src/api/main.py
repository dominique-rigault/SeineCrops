"""API SeineCrops (sprint S5).

Lancement local :
    uvicorn src.api.main:app --reload

Documentation OpenAPI auto-générée : http://localhost:8000/docs
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from . import db
from .queries import fetch_parcelle_detail, fetch_parcelle_profil
from .schemas import ParcelleDetail, ParcelleProfil


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    yield
    await db.close_pool()


app = FastAPI(
    title="SeineCrops API",
    description=(
        "Suivi et classification des cultures par séries temporelles "
        "Sentinel-2 — Plateaux de la Basse-Seine (Caux et Neubourg), Normandie."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/parcelles/{id_parcel}", response_model=ParcelleDetail)
async def get_parcelle(id_parcel: str) -> ParcelleDetail:
    async with db.pool.acquire() as conn:
        fiche = await fetch_parcelle_detail(conn, id_parcel)
    if fiche is None:
        raise HTTPException(status_code=404, detail=f"Parcelle {id_parcel} introuvable")
    return fiche


@app.get("/parcelles/{id_parcel}/profil", response_model=ParcelleProfil)
async def get_parcelle_profil(id_parcel: str) -> ParcelleProfil:
    async with db.pool.acquire() as conn:
        profil = await fetch_parcelle_profil(conn, id_parcel)
    if profil is None:
        raise HTTPException(status_code=404, detail=f"Parcelle {id_parcel} introuvable")
    return profil
