"""
Module d'analyse intelligente - Système de traçabilité RFID (Aptiv)

Contrairement aux anomalies "certaines" détectées par règle fixe dans main.py
(un bac sorti depuis plus de SEUIL_ANOMALIE_MINUTES et jamais arrivé = perte),
ce module utilise un modèle de Machine Learning non supervisé (Isolation
Forest, scikit-learn) pour repérer des motifs anormaux qu'un seuil unique ne
peut pas capturer :

1. detecter_anomalies_consommation()
   Analyse le nombre d'arrivées par ligne et par jour, et apprend
   automatiquement (à partir de l'historique réel en base) ce qu'est un
   rythme "normal" pour chaque ligne. Une journée où une ligne consomme
   anormalement plus (ou moins) que son propre historique est signalée,
   sans seuil codé en dur.

2. detecter_anomalies_delai()
   Analyse le délai de transport dépôt -> ligne de chaque bac, et signale
   les délais statistiquement inhabituels (trop rapides = suspect,
   trop lents = bac probablement en train de se perdre), en tenant compte
   de la ligne et de l'heure de la journée.

Principe de l'Isolation Forest : l'algorithme construit des arbres de
décision aléatoires ; un point de donnée "normal" nécessite en moyenne
beaucoup de coupures pour être isolé des autres, alors qu'un point
anormal (rare, différent) est isolé en très peu de coupures. Le score
d'anomalie mesure donc à quel point une observation est facile à isoler
du reste du groupe.
"""

from collections import defaultdict
from datetime import datetime

from sklearn.ensemble import IsolationForest

# Nombre minimum d'observations nécessaires pour qu'un modèle ait du sens.
# En dessous, on ne peut pas apprendre de motif statistique fiable.
MIN_OBSERVATIONS = 8


def _isolation_scores(X, contamination=0.07):
    """Entraîne un Isolation Forest sur X et renvoie (scores, is_anomaly).
    Score élevé = plus anormal. contamination = proportion attendue
    d'anomalies dans les données (hypothèse raisonnable pour ce contexte)."""
    modele = IsolationForest(n_estimators=200, contamination=contamination, random_state=42)
    modele.fit(X)
    scores = -modele.score_samples(X)  # on inverse : plus haut = plus anormal
    is_anomaly = modele.predict(X) == -1
    return scores, is_anomaly


def detecter_anomalies_consommation(db):
    """
    Regroupe les événements "arrivee" par (jour, ligne) et détecte les
    journées où une ligne consomme anormalement plus ou moins que son
    propre historique, via Isolation Forest.
    """
    from database import Evenement, Lecteur  # import différé pour éviter les imports circulaires

    rows = (
        db.query(Evenement.timestamp, Lecteur.nom)
        .join(Lecteur, Evenement.lecteur_id == Lecteur.id)
        .filter(Evenement.type_evenement == "arrivee")
        .all()
    )

    compteur = defaultdict(int)  # (jour, ligne) -> nb arrivées
    for timestamp, ligne in rows:
        jour = timestamp.date()
        compteur[(jour, ligne)] += 1

    if len(compteur) < MIN_OBSERVATIONS:
        return {
            "disponible": False,
            "raison": f"Historique insuffisant ({len(compteur)} jour(s)x(ligne) observés, {MIN_OBSERVATIONS} minimum requis) pour entraîner un modèle fiable.",
            "anomalies": [],
        }

    lignes = sorted({ligne for (_, ligne) in compteur.keys()})
    ligne_code = {nom: i for i, nom in enumerate(lignes)}

    entries = []
    for (jour, ligne), nb in compteur.items():
        entries.append({
            "jour": jour,
            "ligne": ligne,
            "nb_arrivees": nb,
            "jour_semaine": jour.weekday(),
            "ligne_code": ligne_code[ligne],
        })

    X = [[e["nb_arrivees"], e["ligne_code"], e["jour_semaine"]] for e in entries]
    scores, is_anomaly = _isolation_scores(X)

    resultats = []
    for e, score, anomal in zip(entries, scores, is_anomaly):
        if anomal:
            # Compare à la moyenne historique de CETTE ligne pour donner un chiffre parlant
            valeurs_ligne = [x["nb_arrivees"] for x in entries if x["ligne"] == e["ligne"]]
            moyenne_ligne = sum(valeurs_ligne) / len(valeurs_ligne)
            resultats.append({
                "jour": e["jour"].isoformat(),
                "ligne": e["ligne"],
                "nb_arrivees": e["nb_arrivees"],
                "moyenne_habituelle_ligne": round(moyenne_ligne, 1),
                "ecart": round(e["nb_arrivees"] - moyenne_ligne, 1),
                "score_anomalie": round(float(score), 3),
            })

    resultats.sort(key=lambda r: r["score_anomalie"], reverse=True)
    return {
        "disponible": True,
        "methode": "Isolation Forest (scikit-learn) sur [nb_arrivees, ligne, jour_semaine]",
        "observations_analysees": len(entries),
        "anomalies": resultats,
    }


def detecter_anomalies_delai(db):
    """
    Reconstitue les cycles sortie (Depot) -> arrivee (Ligne) de chaque bac,
    et détecte les délais de transport statistiquement inhabituels via
    Isolation Forest (au lieu d'un seuil fixe unique pour toutes les lignes).
    """
    from database import Evenement, Lecteur, Bac  # import différé

    rows = (
        db.query(Evenement.timestamp, Evenement.type_evenement, Lecteur.nom, Lecteur.type, Bac.tag_id)
        .join(Lecteur, Evenement.lecteur_id == Lecteur.id)
        .join(Bac, Evenement.bac_id == Bac.id)
        .order_by(Evenement.timestamp.asc())
        .all()
    )

    cycles = []
    sortie_ouverte = {}  # tag_id -> timestamp de la dernière sortie en attente

    for timestamp, type_evenement, lecteur_nom, lecteur_type, tag_id in rows:
        if lecteur_type == "depot" and type_evenement == "sortie":
            sortie_ouverte[tag_id] = timestamp
        elif lecteur_type == "ligne" and type_evenement == "arrivee" and tag_id in sortie_ouverte:
            t_sortie = sortie_ouverte.pop(tag_id)
            delai_min = (timestamp - t_sortie).total_seconds() / 60
            cycles.append({
                "tag_id": tag_id,
                "ligne": lecteur_nom,
                "delai_min": delai_min,
                "heure_sortie": t_sortie.hour,
                "jour_semaine": t_sortie.weekday(),
                "t_sortie": t_sortie,
            })

    if len(cycles) < MIN_OBSERVATIONS:
        return {
            "disponible": False,
            "raison": f"Historique insuffisant ({len(cycles)} cycle(s) sortie->arrivee complets) pour entraîner un modèle fiable.",
            "anomalies": [],
        }

    lignes = sorted({c["ligne"] for c in cycles})
    ligne_code = {nom: i for i, nom in enumerate(lignes)}

    X = [[c["delai_min"], c["heure_sortie"], c["jour_semaine"], ligne_code[c["ligne"]]] for c in cycles]
    scores, is_anomaly = _isolation_scores(X, contamination=0.05)

    resultats = []
    for c, score, anomal in zip(cycles, scores, is_anomaly):
        if anomal:
            delais_ligne = [x["delai_min"] for x in cycles if x["ligne"] == c["ligne"]]
            moyenne_ligne = sum(delais_ligne) / len(delais_ligne)
            resultats.append({
                "tag_id": c["tag_id"],
                "ligne": c["ligne"],
                "delai_min": round(c["delai_min"], 1),
                "delai_moyen_ligne": round(moyenne_ligne, 1),
                "sortie_le": c["t_sortie"].isoformat(),
                "score_anomalie": round(float(score), 3),
            })

    resultats.sort(key=lambda r: r["score_anomalie"], reverse=True)
    return {
        "disponible": True,
        "methode": "Isolation Forest (scikit-learn) sur [delai_min, heure_sortie, jour_semaine, ligne]",
        "observations_analysees": len(cycles),
        "anomalies": resultats,
    }
