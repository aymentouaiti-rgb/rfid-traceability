"""
Serveur FastAPI - Système de traçabilité RFID (bacs de matière)

Endpoints principaux :
- POST /scan          : recevoir un événement RFID (depuis ESP32 ou simulateur)
- GET  /bacs          : lister tous les bacs et leur statut
- GET  /bacs/{tag_id} : historique d'un bac précis
- GET  /anomalies     : lister les bacs en anomalie (sortis trop longtemps, règle fixe)
- GET  /anomalies/intelligentes : anomalies détectées par Machine Learning (Isolation Forest)
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import init_db, get_db, Bac, Ouvrier, Lecteur, Evenement
import intelligence

app = FastAPI(title="Système de Traçabilité RFID - Aptiv")

# Autorise le dashboard HTML (ouvert en fichier local ou sur un autre port)
# à interroger cette API depuis le navigateur.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Seuil au-delà duquel un bac "sorti" et jamais arrivé est considéré en anomalie
# (remettre à 0 temporairement si tu veux tester /anomalies immédiatement pendant un essai)
SEUIL_ANOMALIE_MINUTES = 30


@app.on_event("startup")
def on_startup():
    init_db()


# ---------- Schémas de requête ----------

class ScanEvent(BaseModel):
    tag_id: str                  # ID du tag RFID du bac
    lecteur_nom: str              # nom du lecteur, ex: "Depot_1" ou "Ligne_A"
    badge_id: Optional[str] = None  # badge de l'ouvrier (optionnel)


# ---------- Endpoints ----------

@app.post("/scan")
def recevoir_scan(event: ScanEvent, db: Session = Depends(get_db)):
    """
    Reçoit un événement de scan RFID depuis un lecteur (ESP32 ou simulateur).
    Détermine automatiquement si c'est une "sortie" (lecteur type dépôt)
    ou une "arrivee" (lecteur type ligne), et met à jour le statut du bac.
    """
    # 1. Récupérer ou créer le lecteur
    lecteur = db.query(Lecteur).filter(Lecteur.nom == event.lecteur_nom).first()
    if not lecteur:
        raise HTTPException(status_code=404, detail=f"Lecteur '{event.lecteur_nom}' inconnu. Créez-le d'abord via /lecteurs.")

    # 2. Récupérer ou créer le bac (auto-création à la première lecture)
    bac = db.query(Bac).filter(Bac.tag_id == event.tag_id).first()
    if not bac:
        bac = Bac(tag_id=event.tag_id, statut="au_depot")
        db.add(bac)
        db.commit()
        db.refresh(bac)

    # 3. Récupérer l'ouvrier si badge fourni
    ouvrier = None
    if event.badge_id:
        ouvrier = db.query(Ouvrier).filter(Ouvrier.badge_id == event.badge_id).first()
        if not ouvrier:
            ouvrier = Ouvrier(badge_id=event.badge_id)
            db.add(ouvrier)
            db.commit()
            db.refresh(ouvrier)

    # 4. Déterminer le type d'événement selon le type de lecteur
    type_evenement = "sortie" if lecteur.type == "depot" else "arrivee"

    # 5. Enregistrer l'événement
    nouvel_evenement = Evenement(
        bac_id=bac.id,
        ouvrier_id=ouvrier.id if ouvrier else None,
        lecteur_id=lecteur.id,
        type_evenement=type_evenement,
        timestamp=datetime.utcnow(),
    )
    db.add(nouvel_evenement)

    # 6. Mettre à jour le statut du bac
    bac.statut = "sorti" if type_evenement == "sortie" else "a_la_ligne"
    db.commit()

    return {
        "message": f"Scan enregistré : bac {event.tag_id} -> {type_evenement}",
        "bac_id": bac.id,
        "statut": bac.statut,
    }


@app.get("/bacs")
def lister_bacs(db: Session = Depends(get_db)):
    """Liste tous les bacs et leur statut actuel."""
    bacs = db.query(Bac).all()
    return [{"tag_id": b.tag_id, "nom": b.nom, "statut": b.statut} for b in bacs]


@app.get("/bacs/{tag_id}")
def historique_bac(tag_id: str, db: Session = Depends(get_db)):
    """Historique complet des scans pour un bac donné."""
    bac = db.query(Bac).filter(Bac.tag_id == tag_id).first()
    if not bac:
        raise HTTPException(status_code=404, detail="Bac introuvable")

    historique = [
        {
            "type": e.type_evenement,
            "lecteur": e.lecteur.nom,
            "ouvrier": e.ouvrier.badge_id if e.ouvrier else None,
            "timestamp": e.timestamp.isoformat(),
        }
        for e in sorted(bac.evenements, key=lambda x: x.timestamp)
    ]
    return {"tag_id": bac.tag_id, "statut": bac.statut, "historique": historique}


@app.get("/anomalies")
def lister_anomalies(db: Session = Depends(get_db)):
    """
    Détecte les bacs sortis depuis plus de SEUIL_ANOMALIE_MINUTES
    sans être arrivés à une ligne -> anomalie potentielle (perte).
    """
    seuil = datetime.utcnow() - timedelta(minutes=SEUIL_ANOMALIE_MINUTES)
    bacs_sortis = db.query(Bac).filter(Bac.statut == "sorti").all()

    anomalies = []
    for bac in bacs_sortis:
        dernier_evenement = max(bac.evenements, key=lambda x: x.timestamp, default=None)
        if dernier_evenement and dernier_evenement.timestamp < seuil:
            anomalies.append({
                "tag_id": bac.tag_id,
                "sorti_depuis": dernier_evenement.timestamp.isoformat(),
                "minutes_ecoulees": round((datetime.utcnow() - dernier_evenement.timestamp).total_seconds() / 60, 1),
            })

    return {"seuil_minutes": SEUIL_ANOMALIE_MINUTES, "anomalies": anomalies}


# ---------- Détection intelligente (Machine Learning) ----------

@app.get("/anomalies/intelligentes")
def anomalies_intelligentes(db: Session = Depends(get_db)):
    """
    Détection d'anomalies par Machine Learning (Isolation Forest, non supervisé),
    en complément de /anomalies (règle fixe sur les bacs jamais arrivés).

    Deux analyses, apprises automatiquement depuis l'historique réel en base
    (pas de seuil codé en dur) :
    - "consommation" : journées où une ligne consomme anormalement plus/moins
      de bacs que son propre rythme habituel.
    - "delai_transport" : bacs dont le délai dépôt -> ligne est statistiquement
      inhabituel (trop rapide ou trop lent) compte tenu de la ligne et de l'heure.
    """
    return {
        "consommation": intelligence.detecter_anomalies_consommation(db),
        "delai_transport": intelligence.detecter_anomalies_delai(db),
    }


# ---------- Gestion des lecteurs (setup initial) ----------

class LecteurCreate(BaseModel):
    nom: str
    type: str  # "depot" ou "ligne"


@app.post("/lecteurs")
def creer_lecteur(lecteur: LecteurCreate, db: Session = Depends(get_db)):
    """Enregistre un nouveau point de lecture (à faire une fois par lecteur physique)."""
    existant = db.query(Lecteur).filter(Lecteur.nom == lecteur.nom).first()
    if existant:
        raise HTTPException(status_code=400, detail="Ce lecteur existe déjà")

    if lecteur.type not in ("depot", "ligne"):
        raise HTTPException(status_code=400, detail="type doit être 'depot' ou 'ligne'")

    nouveau = Lecteur(nom=lecteur.nom, type=lecteur.type)
    db.add(nouveau)
    db.commit()
    db.refresh(nouveau)
    return {"message": "Lecteur créé", "id": nouveau.id, "nom": nouveau.nom}


@app.get("/lecteurs")
def lister_lecteurs(db: Session = Depends(get_db)):
    return [{"nom": l.nom, "type": l.type} for l in db.query(Lecteur).all()]
