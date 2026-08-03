"""Configuration pytest partagée.

Ajoute la racine du projet à `sys.path` pour permettre `from src.api...`
depuis les fichiers de tests, quel que soit le répertoire d'où `pytest`
est lancé.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
