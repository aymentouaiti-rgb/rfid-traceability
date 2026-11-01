"""
Générateur de dataset - simulation continue sur plusieurs jours.

Insère directement dans la base de données (pas besoin du serveur lancé)
pour pouvoir contrôler les timestamps et générer rapidement un historique
réaliste de plusieurs jours/semaines.

Scénarios inclus :
- Flux normal : bacs qui sortent du dépôt et arrivent à la ligne après un délai réaliste
- Bacs perdus : sortis mais jamais arrivés (anomalie de perte)
- Doubles sorties : un bac scanné deux fois au dépôt sans être revenu (erreur ou vol potentiel)
- Journée de sur-consommation : une ligne consomme anormalement plus de bacs un jour donné

Usage :
    python generer_dataset.py

Résultat :
    - Remplit traceability.db avec l'historique simulé
    - Exporte aussi evenements.csv pour analyse/ML en dehors du serveur
"""

import csv
import random
from datetime import datetime, timedelta

from database import init_db, SessionLocal, Bac, Ouvrier, Lecteur, Evenement, Base, engine

# ---------- Paramètres de simulation ----------

NB_JOURS = 14
LIGNES = ["Ligne_A", "Ligne_B", "Ligne_C"]
NB_BACS = 30
NB_OUVRIERS = 6
HEURE_DEBUT_SHIFT = 7   # 7h
HEURE_FIN_SHIFT = 15    # 15h (shift de 8h)

# Délai normal de transport dépôt -> ligne, en minutes
DELAI_NORMAL_MIN = 3
DELAI_NORMAL_MAX = 12

# Probabilité qu'un bac sorti soit "perdu" (jamais scanné à la ligne) ce jour-là
PROBA_BAC_PERDU = 0.03

# Probabilité qu'un bac soit scanné deux fois au dépôt sans être revenu entre-temps
PROBA_DOUBLE_SORTIE = 0.02

# Jour (index, 0-based) et ligne où on injecte une sur-consommation anormale
JOUR_SURCONSOMMATION = 7
LIGNE_SURCONSOMMATION = "Ligne_B"
MULTIPLICATEUR_SURCONSOMMATION = 3

# Bacs volontairement perdus DÉFINITIVEMENT (sortis un jour donné, jamais revus après)
# -> garantit que /anomalies et le statut final montrent bien des cas concrets
NB_BACS_PERDUS_DEFINITIFS = 2


def setup_referentiel(db):
    """Crée le dépôt, les lignes, les bacs et les ouvriers de référence."""
    lecteurs = {}

    depot = Lecteur(nom="Depot_1", type="depot")
    db.add(depot)
    lecteurs["Depot_1"] = depot

    for ligne in LIGNES:
        l = Lecteur(nom=ligne, type="ligne")
        db.add(l)
        lecteurs[ligne] = l

    db.commit()

    bacs = []
    for i in range(1, NB_BACS + 1):
        b = Bac(tag_id=f"BAC{i:03d}", statut="au_depot")
        db.add(b)
        bacs.append(b)

    ouvriers = []
    for i in range(1, NB_OUVRIERS + 1):
        o = Ouvrier(badge_id=f"OUV{i:03d}")
        db.add(o)
        ouvriers.append(o)

    db.commit()
    return lecteurs, bacs, ouvriers


def heure_aleatoire_dans_shift(jour_date):
    """Génère un timestamp aléatoire pendant le shift de ce jour-là."""
    minute_debut = HEURE_DEBUT_SHIFT * 60
    minute_fin = HEURE_FIN_SHIFT * 60
    minute_tirée = random.randint(minute_debut, minute_fin)
    return jour_date.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=minute_tirée)


def simuler_journee(db, jour_index, jour_date, lecteurs, bacs, ouvriers, evenements_log, bacs_perdus_definitifs, bacs_actifs):
    """Simule tous les mouvements de bacs pour une journée donnée."""

    # Nombre de mouvements "normaux" par ligne ce jour-là
    nb_mouvements_par_ligne = {ligne: random.randint(8, 15) for ligne in LIGNES}

    # Injection de la sur-consommation sur le jour et la ligne ciblés
    if jour_index == JOUR_SURCONSOMMATION:
        nb_mouvements_par_ligne[LIGNE_SURCONSOMMATION] *= MULTIPLICATEUR_SURCONSOMMATION

    for ligne, nb_mouvements in nb_mouvements_par_ligne.items():
        lecteur_ligne = lecteurs[ligne]
        lecteur_depot = lecteurs["Depot_1"]

        for _ in range(nb_mouvements):
            bac = random.choice(bacs_actifs)  # seuls les bacs encore "en circulation" peuvent bouger
            ouvrier = random.choice(ouvriers)

            t_sortie = heure_aleatoire_dans_shift(jour_date)

            # Événement de sortie dépôt
            ev_sortie = Evenement(
                bac_id=bac.id, ouvrier_id=ouvrier.id, lecteur_id=lecteur_depot.id,
                type_evenement="sortie", timestamp=t_sortie,
            )
            db.add(ev_sortie)
            evenements_log.append((bac.tag_id, ouvrier.badge_id, "Depot_1", "sortie", t_sortie))

            # Décider si ce bac est "perdu" ce coup-ci
            est_perdu = random.random() < PROBA_BAC_PERDU

            # Décider s'il y a une double sortie anormale (scanné 2x au dépôt sans arrivée entre les deux)
            est_double_sortie = random.random() < PROBA_DOUBLE_SORTIE

            if est_double_sortie:
                t_double = t_sortie + timedelta(minutes=random.randint(1, 5))
                ev_double = Evenement(
                    bac_id=bac.id, ouvrier_id=random.choice(ouvriers).id, lecteur_id=lecteur_depot.id,
                    type_evenement="sortie", timestamp=t_double,
                )
                db.add(ev_double)
                evenements_log.append((bac.tag_id, ouvrier.badge_id, "Depot_1", "sortie_DOUBLE", t_double))

            if not est_perdu:
                delai = random.randint(DELAI_NORMAL_MIN, DELAI_NORMAL_MAX)
                t_arrivee = t_sortie + timedelta(minutes=delai)
                ev_arrivee = Evenement(
                    bac_id=bac.id, ouvrier_id=None, lecteur_id=lecteur_ligne.id,
                    type_evenement="arrivee", timestamp=t_arrivee,
                )
                db.add(ev_arrivee)
                evenements_log.append((bac.tag_id, None, ligne, "arrivee", t_arrivee))
                bac.statut = "a_la_ligne"
            else:
                bac.statut = "sorti"  # reste "sorti" = anomalie détectable

    db.commit()


def generer():
    # ATTENTION : ce script réinitialise complètement la base (toutes les
    # tables sont supprimées puis recréées vides) avant de générer l'historique.
    # Objectif : garantir un jeu de données propre et reproductible pour la
    # démo/le jury, sans mélange avec d'anciens tests manuels. Si tu veux
    # conserver des données existantes, ne lance pas ce script tel quel.
    print("Réinitialisation de la base de données (traceability.db)...")
    Base.metadata.drop_all(bind=engine)
    init_db()
    db = SessionLocal()

    print(f"=== Génération de {NB_JOURS} jours de simulation sur {len(LIGNES)} lignes ===")
    lecteurs, bacs, ouvriers = setup_referentiel(db)

    date_debut = datetime.now() - timedelta(days=NB_JOURS)
    evenements_log = []

    # Jour où on force la perte définitive de quelques bacs (assez tôt pour qu'ils
    # ne soient jamais réutilisés ensuite, donc bien visibles dans /anomalies à la fin)
    jour_perte_definitive = NB_JOURS - 4
    bacs_perdus_definitifs = random.sample(bacs, NB_BACS_PERDUS_DEFINITIFS)
    bacs_actifs = list(bacs)  # pool des bacs encore "en circulation"

    for jour_index in range(NB_JOURS):
        jour_date = date_debut + timedelta(days=jour_index)
        simuler_journee(db, jour_index, jour_date, lecteurs, bacs, ouvriers, evenements_log, bacs_perdus_definitifs, bacs_actifs)

        # Au jour choisi : ces bacs sortent une dernière fois et ne reviennent jamais
        if jour_index == jour_perte_definitive:
            lecteur_depot = lecteurs["Depot_1"]
            for bac in bacs_perdus_definitifs:
                t_perte = heure_aleatoire_dans_shift(jour_date)
                ouvrier = random.choice(ouvriers)
                ev = Evenement(
                    bac_id=bac.id, ouvrier_id=ouvrier.id, lecteur_id=lecteur_depot.id,
                    type_evenement="sortie", timestamp=t_perte,
                )
                db.add(ev)
                evenements_log.append((bac.tag_id, ouvrier.badge_id, "Depot_1", "sortie_PERTE_DEFINITIVE", t_perte))
                bac.statut = "sorti"
                bacs_actifs.remove(bac)  # retiré du pool : ne bougera plus jusqu'à la fin
            db.commit()

        note = ""
        if jour_index == JOUR_SURCONSOMMATION:
            note = f"  <-- sur-consommation injectée sur {LIGNE_SURCONSOMMATION}"
        if jour_index == jour_perte_definitive:
            noms = ", ".join(b.tag_id for b in bacs_perdus_definitifs)
            note += f"  <-- perte définitive injectée : {noms}"
        print(f"Jour {jour_index + 1}/{NB_JOURS} ({jour_date.date()}) simulé{note}")

    db.close()

    # Export CSV pour analyse / ML en dehors du backend
    with open("evenements.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["tag_id", "badge_id", "lecteur", "type_evenement", "timestamp"])
        for row in evenements_log:
            writer.writerow(row)

    print(f"\n=== Terminé : {len(evenements_log)} événements générés ===")
    print("Base de données : traceability.db")
    print("Export CSV       : evenements.csv")
    print(f"\nAnomalies injectées volontairement :")
    print(f"- Bacs perdus (~{int(PROBA_BAC_PERDU * 100)}% des sorties) : jamais scannés à la ligne")
    print(f"- Doubles sorties (~{int(PROBA_DOUBLE_SORTIE * 100)}%) : scannés 2x au dépôt sans retour entre-temps")
    print(f"- Jour {JOUR_SURCONSOMMATION + 1} : {LIGNE_SURCONSOMMATION} consomme ~{MULTIPLICATEUR_SURCONSOMMATION}x plus que la normale")


if __name__ == "__main__":
    generer()
