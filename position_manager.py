"""
MR EMA - Gestion des positions ouvertes (état persistant)

GitHub Actions ne garde rien entre deux exécutions du cron : chaque run part d'un
environnement neuf. Pour suivre une position sur plusieurs cycles de 10 minutes
(voir si son TP ou son SL a été touché), l'état doit être committé dans le repo
Git lui-même (fichiers JSON dans data/), relu au début de chaque run, et
recommitté à la fin. C'est le rôle de ce module.
"""

import json
import os
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from typing import Optional

import config

logger = logging.getLogger("mr_ema.position_manager")


@dataclass
class Position:
    id: str                      # identifiant unique (ticker + timestamp d'ouverture)
    ticker: str
    ticker_display: str
    asset_type: str
    direction: str                # "ACHAT" ou "VENTE"
    prix_entree: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float]
    take_profit_3: Optional[float]
    rr_tp1: float
    pips_risque: float
    ouverte_le: str               # ISO timestamp UTC
    statut: str = "OUVERTE"       # OUVERTE, TP1_TOUCHE, TP2_TOUCHE, FERMEE_TP, FERMEE_SL, FERMEE_EXPIREE
    tp1_touche: bool = False
    tp2_touche: bool = False
    tp3_touche: bool = False
    resultat_pips: Optional[float] = None
    fermee_le: Optional[str] = None


def _charger_json(chemin: str) -> list:
    if not os.path.exists(chemin):
        return []
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Erreur de lecture {chemin}: {e} - fichier traité comme vide")
        return []


def _sauvegarder_json(chemin: str, donnees: list) -> None:
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=2)


def charger_positions_ouvertes() -> list[Position]:
    donnees = _charger_json(config.STATE_FILE)
    return [Position(**p) for p in donnees]


def sauvegarder_positions_ouvertes(positions: list[Position]) -> None:
    _sauvegarder_json(config.STATE_FILE, [asdict(p) for p in positions])


def charger_historique() -> list[dict]:
    return _charger_json(config.HISTORY_FILE)


def ajouter_a_historique(position: Position) -> None:
    historique = charger_historique()
    historique.append(asdict(position))
    _sauvegarder_json(config.HISTORY_FILE, historique)


def ouvrir_position(ticker: str, ticker_display: str, asset_type: str, signal) -> Position:
    """Crée une nouvelle Position à partir d'un SignalTrade validé par la stratégie."""
    maintenant = datetime.now(timezone.utc).isoformat()
    position_id = f"{ticker}_{maintenant}"

    return Position(
        id=position_id,
        ticker=ticker,
        ticker_display=ticker_display,
        asset_type=asset_type,
        direction=signal.direction,
        prix_entree=signal.niveaux.prix_entree,
        stop_loss=signal.niveaux.stop_loss,
        take_profit_1=signal.niveaux.take_profit_1,
        take_profit_2=signal.niveaux.take_profit_2,
        take_profit_3=signal.niveaux.take_profit_3,
        rr_tp1=signal.niveaux.rr_tp1,
        pips_risque=signal.niveaux.pips_risque,
        ouverte_le=maintenant,
    )


def _niveau_touche(direction: str, prix_actuel: float, niveau: float, est_tp: bool) -> bool:
    """
    Détermine si un niveau (TP ou SL) est touché, selon la direction de la position.
    ACHAT + TP  -> touché si prix_actuel >= niveau
    ACHAT + SL  -> touché si prix_actuel <= niveau
    VENTE + TP  -> touché si prix_actuel <= niveau
    VENTE + SL  -> touché si prix_actuel >= niveau
    """
    if direction == "ACHAT":
        return prix_actuel >= niveau if est_tp else prix_actuel <= niveau
    else:  # VENTE
        return prix_actuel <= niveau if est_tp else prix_actuel >= niveau


def verifier_position(position: Position, prix_actuel: float) -> tuple[Position, list[str]]:
    """
    Vérifie si un TP ou le SL de la position a été touché au prix actuel.
    Retourne la position mise à jour + une liste d'événements textuels à notifier sur Telegram.

    IMPORTANT : le prix_actuel utilisé ici vient de Yahoo Finance, donc différé de ~15-20 min.
    Un TP/SL peut donc être signalé "touché" avec ce même délai par rapport au marché réel.
    Cette limite est documentée et assumée dans tout le projet (voir data_fetcher.py).
    """
    evenements = []

    # --- Stop Loss : priorité absolue, si touché la position est fermée quel que soit l'état des TP ---
    if position.statut not in ("FERMEE_TP", "FERMEE_SL", "FERMEE_EXPIREE"):
        if _niveau_touche(position.direction, prix_actuel, position.stop_loss, est_tp=False):
            position.statut = "FERMEE_SL"
            position.resultat_pips = -position.pips_risque
            position.fermee_le = datetime.now(timezone.utc).isoformat()
            evenements.append("SL_TOUCHE")
            return position, evenements

    # --- Take Profits, dans l'ordre ---
    if not position.tp1_touche and _niveau_touche(position.direction, prix_actuel, position.take_profit_1, est_tp=True):
        position.tp1_touche = True
        evenements.append("TP1_TOUCHE")
        if position.take_profit_2 is None and position.take_profit_3 is None:
            # Un seul TP prévu pour cette position -> clôture complète ici
            position.statut = "FERMEE_TP"
            position.resultat_pips = position.rr_tp1 * position.pips_risque
            position.fermee_le = datetime.now(timezone.utc).isoformat()
        else:
            position.statut = "TP1_TOUCHE"

    if position.take_profit_2 is not None and not position.tp2_touche:
        if _niveau_touche(position.direction, prix_actuel, position.take_profit_2, est_tp=True):
            position.tp2_touche = True
            evenements.append("TP2_TOUCHE")
            if position.take_profit_3 is None:
                position.statut = "FERMEE_TP"
                position.resultat_pips = position.rr_tp1 * position.pips_risque  # approximation prudente
                position.fermee_le = datetime.now(timezone.utc).isoformat()
            else:
                position.statut = "TP2_TOUCHE"

    if position.take_profit_3 is not None and not position.tp3_touche:
        if _niveau_touche(position.direction, prix_actuel, position.take_profit_3, est_tp=True):
            position.tp3_touche = True
            evenements.append("TP3_TOUCHE")
            position.statut = "FERMEE_TP"
            position.resultat_pips = position.rr_tp1 * position.pips_risque
            position.fermee_le = datetime.now(timezone.utc).isoformat()

    return position, evenements


def verifier_expiration_day_trading(position: Position) -> bool:
    """
    Vérifie si une position dépasse la durée maximale autorisée (day trading strict,
    voir config.MAX_POSITION_HOURS). Si oui, elle doit être clôturée au prix du marché,
    peu importe si elle est en profit ou en perte à ce moment.
    """
    ouverte = datetime.fromisoformat(position.ouverte_le)
    maintenant = datetime.now(timezone.utc)
    duree_heures = (maintenant - ouverte).total_seconds() / 3600
    return duree_heures >= config.MAX_POSITION_HOURS
