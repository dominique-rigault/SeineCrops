"""API SeineCrops (sprint S5).

Lancement local :
    uvicorn src.api.main:app --reload

Documentation OpenAPI auto-générée : http://localhost:8000/docs
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import db
from .queries import (
    BboxTropLargeError,
    LIMIT_DEFAUT,
    fetch_parcelle_detail,
    fetch_parcelle_profil,
    fetch_parcelles_bbox,
)
from .schemas import ParcelleDetail, ParcelleListResponse, ParcelleProfil


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

# Permissif pour le développement local (carte web servie depuis un port
# différent de l'API). À restreindre à l'origine réelle avant tout
# déploiement public (cf. methode.md §S5, Hors périmètre -> S6/perspectives).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
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


@app.get("/parcelles", response_model=ParcelleListResponse)
async def get_parcelles_bbox(
    bbox: str = Query(
        ...,
        description="xmin,ymin,xmax,ymax en EPSG:4326 (ex. la fenêtre courante de la carte)",
        examples=["0.72,49.39,0.79,49.42"],
    ),
    limit: int = Query(LIMIT_DEFAUT, gt=0, le=LIMIT_DEFAUT),
) -> ParcelleListResponse:
    try:
        xmin, ymin, xmax, ymax = (float(v) for v in bbox.split(","))
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="bbox invalide, format attendu : xmin,ymin,xmax,ymax",
        ) from exc

    try:
        async with db.pool.acquire() as conn:
            return await fetch_parcelles_bbox(conn, (xmin, ymin, xmax, ymax), limit)
    except BboxTropLargeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
