"""Schémas Pydantic de l'API SeineCrops.

Cf. `cadrage/methode.md` §S5 pour le contrat de données et le mapping
colonne DB → champ API. Validés sur données réelles en `06_api.ipynb`
§6.3 (ParcelleDetail) et §6.4 (ParcelleProfil).
"""

from datetime import date

from pydantic import BaseModel


class ParcelleDetail(BaseModel):
    id_parcel: str
    code_cultu_declare: str
    classe_declaree: str
    classe_predite: str
    proba_classe: float
    score_divergence: float
    divergente: bool
    zone_raccord_orbital: bool
    sos: date | None
    pos: date | None
    eos: date | None
    los_jours: int | None
    phenologie_fiable: bool


class ParcelleProfil(BaseModel):
    id_parcel: str
    dates: list[date]  # 16 pas mensuels, sept N → déc N+1
    ndvi: list[float | None]
    evi: list[float | None]
    ndwi: list[float | None]
    ndre: list[float | None]
