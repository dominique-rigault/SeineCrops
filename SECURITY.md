# Politique de sécurité

## Périmètre

Ce dépôt implémente une chaîne reproductible de suivi et classification des cultures
par séries temporelles Sentinel-2 sur les plateaux de la Basse-Seine (Normandie),
incluant une base PostGIS, une API FastAPI et une carte web interactive.

Sont considérées comme vulnérabilités pertinentes :

- Secrets exposés : identifiants Copernicus Data Space Ecosystem (OAuth),
  chaînes de connexion PostgreSQL/PostGIS, tokens d'API, clés ou mots de passe
  présents dans le code, les fichiers de configuration ou l'historique Git.
- Dépendances Python (Rasterio, scikit-learn, PyTorch/Keras, FastAPI…)
  présentant une CVE connue.
- Failles de l'API FastAPI : injection SQL via les requêtes PostGIS, absence
  de validation des entrées, exposition non intentionnelle de données.
- Failles dans une carte web interactive (XSS, injection côté client).
- Données personnelles accidentellement incluses (identifiants d'exploitants
  agricoles issus du RPG, par exemple).

## Signaler une vulnérabilité

Merci de **ne pas ouvrir d'issue publique** pour les signalements de sécurité.

Deux options :

1. **Via GitHub** (recommandé) : utilisez le bouton **"Report a vulnerability"**
   dans l'onglet **Security** du dépôt. Cela crée un advisory privé avec suivi intégré.
2. **Par email** : **dominique.rigault@outlook.com**, si vous n'avez pas de compte GitHub.

Indiquez si possible :

- La nature du problème (secret exposé, dépendance vulnérable, faille API,
  faille côté client…).
- Le fichier, l'endpoint ou le commit concerné.
- Les étapes pour reproduire le problème.

Je m'engage à accuser réception sous 7 jours et à traiter le signalement
dans un délai raisonnable.

## Divulgation

Une fois le problème corrigé, un résumé pourra être publié dans les notes de version
du dépôt. Les contributeurs qui signalent des vulnérabilités seront crédités
(sauf demande contraire).
