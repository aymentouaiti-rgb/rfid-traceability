"""
Modèle de base de données pour le système de traçabilité RFID.
Tables : Bac, Ouvrier, Lecteur, Evenement
"""

from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Float
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATABASE_URL = "sqlite:///./traceability.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Bac(Base):
    """Un bac de matière (connecteurs, chutes, etc.) identifié par un tag RFID unique."""
    __tablename__ = "bacs"

    id = Column(Integer, primary_key=True, index=True)
    tag_id = Column(String, unique=True, index=True, nullable=False)  # ID lu depuis le tag RFID
    nom = Column(String, nullable=True)  # ex: "Connecteurs type A"
    statut = Column(String, default="au_depot")  # au_depot | sorti | a_la_ligne | anomalie

    evenements = relationship("Evenement", back_populates="bac")


class Ouvrier(Base):
    """Un ouvrier identifié par un badge RFID."""
    __tablename__ = "ouvriers"

    id = Column(Integer, primary_key=True, index=True)
    badge_id = Column(String, unique=True, index=True, nullable=False)
    nom = Column(String, nullable=True)

    evenements = relationship("Evenement", back_populates="ouvrier")


class Lecteur(Base):
    """Un point de lecture RFID physique (dépôt ou ligne)."""
    __tablename__ = "lecteurs"

    id = Column(Integer, primary_key=True, index=True)
    nom = Column(String, nullable=False)  # ex: "Depot_1", "Ligne_A"
    type = Column(String, nullable=False)  # "depot" ou "ligne"

    evenements = relationship("Evenement", back_populates="lecteur")


class Evenement(Base):
    """Un scan RFID : un bac vu par un lecteur, associé à un badge ouvrier (optionnel)."""
    __tablename__ = "evenements"

    id = Column(Integer, primary_key=True, index=True)
    bac_id = Column(Integer, ForeignKey("bacs.id"), nullable=False)
    ouvrier_id = Column(Integer, ForeignKey("ouvriers.id"), nullable=True)
    lecteur_id = Column(Integer, ForeignKey("lecteurs.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    type_evenement = Column(String, nullable=False)  # "sortie" ou "arrivee"

    bac = relationship("Bac", back_populates="evenements")
    ouvrier = relationship("Ouvrier", back_populates="evenements")
    lecteur = relationship("Lecteur", back_populates="evenements")


def init_db():
    """Crée les tables si elles n'existent pas déjà."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency FastAPI : fournit une session DB et la ferme après usage."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
