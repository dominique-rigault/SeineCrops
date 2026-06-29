-- SeineCrops · initialisation de la base PostGIS
-- Prérequis : PostgreSQL 18 + PostGIS 3.6
-- Usage : psql -U postgres -d seinecrops -f src/db/init.sql

-- Extension spatiale
CREATE EXTENSION IF NOT EXISTS postgis;

-- Schéma brut : données source non modifiées (géométrie native EPSG:2154)
CREATE SCHEMA IF NOT EXISTS raw;

-- Schéma dérivé : reprojections, clips, transformations
CREATE SCHEMA IF NOT EXISTS derived;
