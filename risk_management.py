"""
MR EMA - Calcul des pips et gestion stricte du risque

RÈGLE NON-NÉGOCIABLE DU PROJET :
Le ratio Risque:Récompense (RR) de CHAQUE position envoyée doit être compris
STRICTEMENT entre 1.60 et 3.20. En dehors de cet intervalle, le signal n'est
jamais envoyé, peu importe la qualité apparente du setup technique.

CALCUL DES PIPS PAR TYPE D'ACTIF :
- Forex "classique" (ex: EUR/USD, GBP/USD) : 1 pip = 0.0001 (4e décimale)
- Paires avec le JPY (ex: USD/JPY, EUR/JPY) : 1 pip = 0.01 (2e décimale) - le yen a 2 décimales, pas 4
- Métaux (XAU/USD, XAG/USD) : convention marché = 1 pip = 0.01 (variable selon brokers, mais
  c'est la convention la plus répandue et celle utilisée ici)
- Crypto (BTC/USD etc.) : pas de "pip" au sens Forex - on exprime le mouvement en USD directement
"""

from dataclasses import dataclass
from typing import Optional
import config


def valeur_pip(asset_type: str, ticker_symbol: str) -> float:
    """
    Retourne la valeur d'1 pip en unité de prix, selon le type d'actif.

    Args:
        asset_type: "forex", "metal", ou "crypto" (voir config.ASSETS)
        ticker_symbol: le symbole affiché (ex: "USDJPY", "XAUUSD") pour détecter le cas JPY
    """
    if asset_type == "crypto":
        # Pas de notion de pip pour les cryptos - on retourne 1 pour exprimer les mouvements en $ bruts
        return 1.0

    if asset_type == "metal":
        return 0.01  # convention XAU/XAG

    if asset_type == "forex":
        if "JPY" in ticker_symbol.upper():
            return 0.01
        return 0.0001

    raise ValueError(f"Type d'actif inconnu: {asset_type}")


def calculer_pips(prix_entree: float, prix_sortie: float, asset_type: str, ticker_symbol: str) -> float:
    """
    Calcule le nombre de pips entre deux prix (peut être négatif si c'est une perte).
    Pour les cryptos, retourne directement la différence en USD (pas de "pip" au sens Forex).
    """
    pip_val = valeur_pip(asset_type, ticker_symbol)
    diff = prix_sortie - prix_entree

    if asset_type == "crypto":
        return round(diff, 2)  # en USD

    return round(diff / pip_val, 1)


@dataclass
class NiveauxPosition:
    """Résultat du calcul des niveaux d'une position (entrée, SL, TP multiples, RR)."""
    direction: str            # "ACHAT" ou "VENTE"
    prix_entree: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float]
    take_profit_3: Optional[float]
    rr_tp1: float
    rr_tp2: Optional[float]
    rr_tp3: Optional[float]
    pips_risque: float
    pips_tp1: float
    pips_tp2: Optional[float]
    pips_tp3: Optional[float]


def construire_niveaux(direction: str, prix_entree: float, atr: float, asset_type: str,
                        ticker_symbol: str, contexte_marche: str = "normal") -> Optional[NiveauxPosition]:
    """
    Construit les niveaux SL/TP d'une position à partir de l'ATR, puis VALIDE que le RR
    de chaque TP tombe dans l'intervalle autorisé [1.60, 3.20]. Si le TP1 lui-même ne
    respecte pas l'intervalle, retourne None (aucun signal ne doit être envoyé).

    Args:
        direction: "ACHAT" ou "VENTE"
        prix_entree: prix d'entrée proposé
        atr: valeur ATR actuelle sur le timeframe d'entrée
        asset_type: "forex", "metal", "crypto"
        ticker_symbol: symbole pour la détection JPY
        contexte_marche: "normal", "volatil", "range" -> ajuste si TP2/TP3 sont proposés
                          (en marché "range", un seul TP est plus prudent -> pas de sur-extension)
    """
    sl_distance = atr * config.ATR_SL_MULTIPLIER
    tp1_distance = atr * config.ATR_TP1_MULTIPLIER
    tp2_distance = atr * config.ATR_TP2_MULTIPLIER
    tp3_distance = atr * config.ATR_TP3_MULTIPLIER

    if direction == "ACHAT":
        stop_loss = prix_entree - sl_distance
        tp1 = prix_entree + tp1_distance
        tp2 = prix_entree + tp2_distance
        tp3 = prix_entree + tp3_distance
    elif direction == "VENTE":
        stop_loss = prix_entree + sl_distance
        tp1 = prix_entree - tp1_distance
        tp2 = prix_entree - tp2_distance
        tp3 = prix_entree - tp3_distance
    else:
        raise ValueError(f"Direction invalide: {direction}")

    # RR = distance récompense / distance risque (identique quelle que soit la direction, distances toujours positives)
    rr_tp1 = round(tp1_distance / sl_distance, 2)
    rr_tp2 = round(tp2_distance / sl_distance, 2)
    rr_tp3 = round(tp3_distance / sl_distance, 2)

    # --- VALIDATION STRICTE : le TP1 doit être dans l'intervalle autorisé ---
    # C'est la garde-fou principale : si même le premier take-profit ne respecte pas
    # 1.60 <= RR <= 3.20, on annule tout le signal (retour None = "ne pas envoyer").
    if not (config.MIN_RISK_REWARD <= rr_tp1 <= config.MAX_RISK_REWARD):
        return None

    # TP2 et TP3 ne sont inclus QUE s'ils restent eux aussi dans l'intervalle autorisé,
    # ET seulement si le contexte de marché ne suggère pas un range serré (sur-extension inutile)
    inclure_tp2 = (config.MIN_RISK_REWARD <= rr_tp2 <= config.MAX_RISK_REWARD) and contexte_marche != "range"
    inclure_tp3 = (config.MIN_RISK_REWARD <= rr_tp3 <= config.MAX_RISK_REWARD) and contexte_marche == "volatil"

    pips_risque = abs(calculer_pips(prix_entree, stop_loss, asset_type, ticker_symbol))
    pips_tp1 = abs(calculer_pips(prix_entree, tp1, asset_type, ticker_symbol))
    pips_tp2 = abs(calculer_pips(prix_entree, tp2, asset_type, ticker_symbol)) if inclure_tp2 else None
    pips_tp3 = abs(calculer_pips(prix_entree, tp3, asset_type, ticker_symbol)) if inclure_tp3 else None

    return NiveauxPosition(
        direction=direction,
        prix_entree=round(prix_entree, 5),
        stop_loss=round(stop_loss, 5),
        take_profit_1=round(tp1, 5),
        take_profit_2=round(tp2, 5) if inclure_tp2 else None,
        take_profit_3=round(tp3, 5) if inclure_tp3 else None,
        rr_tp1=rr_tp1,
        rr_tp2=rr_tp2 if inclure_tp2 else None,
        rr_tp3=rr_tp3 if inclure_tp3 else None,
        pips_risque=pips_risque,
        pips_tp1=pips_tp1,
        pips_tp2=pips_tp2,
        pips_tp3=pips_tp3,
    )
