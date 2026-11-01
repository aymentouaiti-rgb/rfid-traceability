"""
Simulateur RFID - simule des scans de bacs comme si un ESP32 les envoyait.

Usage :
    python simulateur.py

Ce script simule :
- Plusieurs bacs qui sortent du dépôt avec un badge ouvrier
- La plupart arrivent normalement à la ligne après un délai réaliste
- Un bac volontairement "perdu" (jamais scanné à la ligne) pour tester /anomalies
"""

import random
import time
from datetime import datetime

import requests

SERVER_URL = "http://127.0.0.1:8000"

BACS = [f"BAC{i:03d}" for i in range(1, 11)]  # BAC001 ... BAC010
OUVRIERS = [f"OUV{i:03d}" for i in range(1, 4)]  # OUV001, OUV002, OUV003


def setup_lecteurs():
    """Crée les lecteurs Depot_1 et Ligne_A s'ils n'existent pas déjà."""
    for nom, type_ in [("Depot_1", "depot"), ("Ligne_A", "ligne")]:
        r = requests.post(f"{SERVER_URL}/lecteurs", json={"nom": nom, "type": type_})
        if r.status_code == 200:
            print(f"Lecteur créé : {nom}")
        else:
            print(f"Lecteur déjà existant ou erreur : {nom} -> {r.json()}")


def scan(tag_id, lecteur_nom, badge_id=None):
    payload = {"tag_id": tag_id, "lecteur_nom": lecteur_nom}
    if badge_id:
        payload["badge_id"] = badge_id
    r = requests.post(f"{SERVER_URL}/scan", json=payload)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {tag_id} @ {lecteur_nom} -> {r.status_code} {r.json()}")


def simuler():
    print("=== Setup des lecteurs ===")
    setup_lecteurs()

    print("\n=== Simulation des sorties dépôt ===")
    # Tous les bacs sortent du dépôt, sauf le dernier qui sera volontairement "perdu"
    bac_perdu = BACS[-1]

    for bac in BACS:
        ouvrier = random.choice(OUVRIERS)
        scan(bac, "Depot_1", ouvrier)
        time.sleep(0.3)

    print("\n=== Simulation des arrivées à la ligne (sauf le bac perdu) ===")
    for bac in BACS:
        if bac == bac_perdu:
            print(f"[SIMULATION] {bac} ne sera jamais scanné à la ligne (bac perdu volontaire)")
            continue
        time.sleep(random.uniform(0.5, 1.5))  # délai réaliste de transport
        scan(bac, "Ligne_A")

    print(f"\n=== Terminé. {bac_perdu} devrait apparaître dans /anomalies (une fois le seuil de temps dépassé) ===")
    print("Note : le seuil par défaut dans main.py est de 30 minutes.")
    print("Pour tester /anomalies immédiatement, réduis SEUIL_ANOMALIE_MINUTES à 0 dans main.py le temps du test.")


if __name__ == "__main__":
    simuler()
