# Image Airflow personnalisée pour SeineCrops.
#
# Ajoute à l'image officielle : les dépendances géospatiales/ML du projet
# (installées via le fichier de contraintes officiel Airflow, pour éviter
# tout conflit de version avec les dépendances internes d'Airflow), et
# `psql` (client PostgreSQL en ligne de commande), nécessaire car
# `rpg.py::_executer_psql` (§ ingestion RPG) lance `psql` en sous-processus
# pour charger le dump PGDUMP, ce n'est pas juste une dépendance Python.

FROM apache/airflow:2.10.3-python3.11

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

USER airflow
COPY requirements-airflow.txt /requirements-airflow.txt
RUN pip install --no-cache-dir \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.3/constraints-3.11.txt" \
    -r /requirements-airflow.txt
