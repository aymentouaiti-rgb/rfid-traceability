# Système intelligent de traçabilité RFID pour bacs de matière

Système de traçabilité qui suit chaque bac de matière (connecteurs, chutes de câbles) entre un dépôt et des lignes de production, et détecte automatiquement les anomalies grâce à un modèle d'apprentissage non supervisé (Isolation Forest).

> Le projet fonctionne entièrement en simulation (ESP32 + RC522 sous Wokwi) avec des données générées.

## Architecture

```
Tag RFID → ESP32 + RC522 → HTTP POST /scan → FastAPI → SQLite
                                                  ↓
                              Dashboard web ← Isolation Forest
```

| Composant | Technologie |
|---|---|
| Lecteur + microcontrôleur | ESP32 + RC522 (simulé sous Wokwi, PlatformIO) |
| Backend | FastAPI + SQLAlchemy |
| Base de données | SQLite |
| Détection d'anomalies | règle fixe + Isolation Forest (scikit-learn) |
| Supervision | Dashboard HTML/JS (rafraîchissement toutes les 3 s) |

## Structure du dépôt

```
rfid-traceability/
├── main.py              # API FastAPI
├── database.py          # Modèles SQLAlchemy (Bac, Lecteur, Ouvrier, Événement)
├── intelligence.py      # Détection d'anomalies (Isolation Forest)
├── generer_dataset.py   # Génère 14 jours d'historique avec anomalies injectées
├── simulateur.py        # Simulation de scans
├── dashboard.html       # Tableau de bord de supervision
├── requirements.txt
└── firmware/            # Firmware ESP32 (PlatformIO + Wokwi)
    ├── src/
    ├── platformio.ini
    ├── diagram.json
    └── wokwi.toml
```

## Installation

```bash
python -m venv venv
source venv/bin/activate        # Windows : venv\Scripts\activate
pip install -r requirements.txt
```

## Lancement

**1. Générer le jeu de données** (réinitialise la base à chaque exécution) :

```bash
python generer_dataset.py
```

**2. Démarrer le serveur :**

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

- Documentation Swagger : http://localhost:8000/docs
- Dashboard : ouvrir `dashboard.html` dans un navigateur

**3. Simulation ESP32 (optionnel) :**

```bash
cd firmware
pio run
```

Puis démarrer la simulation avec l'extension Wokwi pour VS Code. L'ESP32 envoie ses scans à `host.wokwi.internal:8000`.

## Endpoints de l'API

| Endpoint | Méthode | Rôle |
|---|---|---|
| `/scan` | POST | Enregistrer un scan RFID et mettre à jour le statut du bac |
| `/bacs` | GET | Lister les bacs et leur statut |
| `/bacs/{tag_id}` | GET | Historique d'un bac |
| `/anomalies` | GET | Bacs sortis depuis plus de 30 min sans arriver (règle fixe) |
| `/anomalies/intelligentes` | GET | Anomalies détectées par Isolation Forest |
| `/lecteurs` | GET / POST | Gestion des points de lecture |

## Détection d'anomalies

Deux analyses par Isolation Forest, sans seuil codé en dur :

- **Consommation** : nombre d'arrivées par jour et par ligne
- **Délais de transport** : durée sortie dépôt → arrivée ligne

Sur le jeu de données généré, le modèle retrouve la sur-consommation injectée sans qu'on lui indique où elle se trouve.

