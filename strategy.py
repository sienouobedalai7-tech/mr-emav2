"""
MR EMA - Logique de stratégie multi-timeframe

Architecture :
  1. TENDANCE (H1)  : EMA50/EMA200 comme support/résistance dynamique -> le prix de
                       clôture doit être au-dessus des DEUX EMA (achat) ou en dessous
                       des DEUX (vente) pour déterminer le biais directionnel autorisé
  2. MOMENTUM (M15) : MACD -> confirme que l'élan va dans le sens de la tendance H1
  3. TIMING (M15)   : TDI -> détermine le point d'entrée précis (croisement price/signal line)
  4. RISQUE (M15)   : ATR -> calibre SL/TP, puis risk_management valide le ratio RR

Un signal n'est retourné QUE si les 4 conditions sont simultanément remplies.
Ce n'est pas une garantie de gain (aucune stratégie technique n'en est une) : c'est
un filtre qui rejette la majorité des cas pour ne garder que les setups où tendance,
momentum et timing sont alignés.
"""

from dataclasses import dataclass
from typing import Optional
import pandas as pd

import config
import indicators
import risk_management


@dataclass
class SignalTrade:
    direction: str
    niveaux: risk_management.NiveauxPosition
    prix_analyse: float
    contexte_marche: str
    raison_ema: str
    raison_macd: str
    raison_tdi: str
    atr_valeur: float


def _detecter_tendance_ema(df_trend_avec_indicateurs: pd.DataFrame) -> Optional[str]:
    """
    Détermine le biais de tendance sur H1 en utilisant EMA50/EMA200 comme zone de
    support/résistance dynamique (et non plus un simple croisement EMA50 vs EMA200).

    Règle :
      - Prix de clôture AU-DESSUS des deux EMA (EMA50 et EMA200) -> tendance ACHAT
      - Prix de clôture EN DESSOUS des deux EMA -> tendance VENTE
      - Prix situé ENTRE les deux EMA (au-dessus de l'une, en dessous de l'autre)
        -> pas de tendance claire, on ne trade pas (zone de transition/range)

    On utilise le prix de CLÔTURE (pas les mèches High/Low) pour éviter qu'une simple
    mèche qui effleure une EMA ne déclenche un faux signal de tendance.
    """
    derniere = df_trend_avec_indicateurs.iloc[-1]
    ema_fast = derniere["ema_fast"]
    ema_slow = derniere["ema_slow"]
    prix_cloture = derniere["Close"]

    if pd.isna(ema_fast) or pd.isna(ema_slow) or pd.isna(prix_cloture):
        return None

    niveau_haut = max(ema_fast, ema_slow)
    niveau_bas = min(ema_fast, ema_slow)

    if prix_cloture > niveau_haut:
        return "ACHAT"
    if prix_cloture < niveau_bas:
        return "VENTE"

    # Prix coincé entre les deux EMA -> tendance pas assez claire, on ne trade pas
    return None


def _confirmer_momentum_macd(df_entry_avec_indicateurs: pd.DataFrame, direction_attendue: str,
                              fenetre_detection: int = 5) -> bool:
    """
    Vérifie que le MACD (M15) confirme la direction donnée par la tendance H1.
    Confirmation = croisement macd_line/signal_line dans le bon sens sur l'une des
    `fenetre_detection` dernières bougies, OU histogramme actuel déjà positif/négatif
    et croissant dans ce sens (élan déjà en cours, pas besoin d'un croisement récent).
    """
    donnees = df_entry_avec_indicateurs.tail(fenetre_detection + 1)
    if donnees["macd_line"].isna().any() or donnees["macd_signal"].isna().any() or len(donnees) < 2:
        return False

    derniere = donnees.iloc[-1]
    precedente = donnees.iloc[-2]

    histogramme_croissant_positif = derniere["macd_histogram"] > 0 and \
                                     derniere["macd_histogram"] > precedente["macd_histogram"]
    histogramme_decroissant_negatif = derniere["macd_histogram"] < 0 and \
                                       derniere["macd_histogram"] < precedente["macd_histogram"]

    if direction_attendue == "ACHAT" and histogramme_croissant_positif:
        return True
    if direction_attendue == "VENTE" and histogramme_decroissant_negatif:
        return True

    macd_line = donnees["macd_line"]
    signal_line = donnees["macd_signal"]

    for i in range(len(donnees) - 1, 0, -1):
        croisement_haussier = (macd_line.iloc[i - 1] <= signal_line.iloc[i - 1]) and \
                               (macd_line.iloc[i] > signal_line.iloc[i])
        croisement_baissier = (macd_line.iloc[i - 1] >= signal_line.iloc[i - 1]) and \
                               (macd_line.iloc[i] < signal_line.iloc[i])

        if direction_attendue == "ACHAT" and croisement_haussier:
            return True
        if direction_attendue == "VENTE" and croisement_baissier:
            return True

    return False


def _confirmer_timing_tdi(df_entry_avec_indicateurs: pd.DataFrame, direction_attendue: str,
                           fenetre_detection: int = 5, fenetre_survente_avant: int = 8) -> bool:
    """
    Vérifie le TDI (M15) pour le timing d'entrée précis.
    Signal ACHAT : la ligne verte (price_line) croise au-dessus de la ligne rouge (signal_line)
                   ET le RSI était en zone de survente PEU AVANT ce croisement précis
    Signal VENTE : croisement inverse, RSI en surachat peu avant

    Le croisement est cherché sur les `fenetre_detection` dernières bougies (pas uniquement
    la toute dernière) pour ne pas rater un signal d'un cycle cron à l'autre. La condition de
    survente/surachat est vérifiée sur une fenêtre qui se termine AU MOMENT du croisement
    détecté (pas sur les dernières bougies dans l'absolu) : un rebond peut être si rapide que
    le RSI n'est déjà plus en survente 2-3 bougies plus tard, alors qu'il l'était juste avant
    le croisement lui-même. C'est cette relation temporelle précise qui doit être vérifiée.
    """
    cols_requises = ["tdi_price_line", "tdi_signal_line", "tdi_rsi"]
    donnees = df_entry_avec_indicateurs.tail(fenetre_detection + fenetre_survente_avant)
    if donnees[cols_requises].isna().any().any() or len(donnees) < 2:
        return False

    price_line = donnees["tdi_price_line"]
    signal_line = donnees["tdi_signal_line"]
    rsi = donnees["tdi_rsi"]

    # On cherche un croisement sur chacune des `fenetre_detection` dernières bougies,
    # en partant de la plus récente vers la plus ancienne (on garde le plus récent trouvé).
    for i in range(len(donnees) - 1, len(donnees) - 1 - fenetre_detection, -1):
        if i < 1:
            break

        croisement_haussier = (price_line.iloc[i - 1] <= signal_line.iloc[i - 1]) and \
                               (price_line.iloc[i] > signal_line.iloc[i])
        croisement_baissier = (price_line.iloc[i - 1] >= signal_line.iloc[i - 1]) and \
                               (price_line.iloc[i] < signal_line.iloc[i])

        # Fenêtre de survente/surachat évaluée JUSTE AVANT le point de croisement trouvé,
        # pas sur les dernières bougies dans l'absolu.
        debut_fenetre = max(0, i - fenetre_survente_avant)
        rsi_avant_croisement = rsi.iloc[debut_fenetre:i + 1]

        if direction_attendue == "ACHAT" and croisement_haussier:
            if (rsi_avant_croisement < 35).any():
                return True
        elif direction_attendue == "VENTE" and croisement_baissier:
            if (rsi_avant_croisement > 65).any():
                return True

    return False


def _detecter_contexte_marche(df_entry_avec_indicateurs: pd.DataFrame) -> str:
    """
    Détermine si le marché est "normal", "volatil", ou en "range", via l'ATR récent
    comparé à sa propre moyenne. Sert à décider si TP2/TP3 sont pertinents.
    """
    atr_recent = df_entry_avec_indicateurs["atr"].tail(20)
    if atr_recent.isna().all():
        return "normal"

    atr_actuel = atr_recent.iloc[-1]
    atr_moyen = atr_recent.mean()

    if pd.isna(atr_actuel) or pd.isna(atr_moyen) or atr_moyen == 0:
        return "normal"

    ratio = atr_actuel / atr_moyen

    if ratio > 1.3:
        return "volatil"
    if ratio < 0.7:
        return "range"
    return "normal"


def analyser_actif(ticker_symbol: str, asset_type: str, df_trend: pd.DataFrame,
                    df_entry: pd.DataFrame) -> Optional[SignalTrade]:
    """
    Point d'entrée principal : applique la stratégie complète multi-timeframe sur un actif.
    Retourne un SignalTrade UNIQUEMENT si les 4 conditions (EMA, MACD, TDI, RR valide) sont réunies.
    """
    df_trend_ind = indicators.calculer_tous_indicateurs(
        df_trend, config.EMA_FAST, config.EMA_SLOW,
        config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL,
        config.ATR_PERIOD, config.TDI_RSI_PERIOD, config.TDI_RSI_PRICE_LINE,
        config.TDI_TRADE_SIGNAL_LINE, config.TDI_VOLATILITY_BAND,
    )
    df_entry_ind = indicators.calculer_tous_indicateurs(
        df_entry, config.EMA_FAST, config.EMA_SLOW,
        config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL,
        config.ATR_PERIOD, config.TDI_RSI_PERIOD, config.TDI_RSI_PRICE_LINE,
        config.TDI_TRADE_SIGNAL_LINE, config.TDI_VOLATILITY_BAND,
    )

    # --- 1. TENDANCE (H1) ---
    direction = _detecter_tendance_ema(df_trend_ind)
    if direction is None:
        return None  # pas de tendance claire -> on ne trade pas ce marché en range sur H1

    # --- 2. MOMENTUM (M15) ---
    if not _confirmer_momentum_macd(df_entry_ind, direction):
        return None

    # --- 3. TIMING (M15) ---
    if not _confirmer_timing_tdi(df_entry_ind, direction):
        return None

    # --- 4. RISQUE : construction des niveaux + validation stricte du RR ---
    derniere_entry = df_entry_ind.iloc[-1]
    prix_actuel = float(derniere_entry["Close"])
    atr_valeur = float(derniere_entry["atr"])

    if pd.isna(atr_valeur) or atr_valeur <= 0:
        return None

    contexte = _detecter_contexte_marche(df_entry_ind)

    niveaux = risk_management.construire_niveaux(
        direction=direction,
        prix_entree=prix_actuel,
        atr=atr_valeur,
        asset_type=asset_type,
        ticker_symbol=ticker_symbol,
        contexte_marche=contexte,
    )

    if niveaux is None:
        # Le RR du TP1 ne respecte pas l'intervalle [1.60, 3.20] -> AUCUN signal envoyé,
        # même si EMA/MACD/TDI étaient tous alignés. Le risk management est non-négociable.
        return None

    return SignalTrade(
        direction=direction,
        niveaux=niveaux,
        prix_analyse=prix_actuel,
        contexte_marche=contexte,
        raison_ema=f"Prix {'au-dessus' if direction == 'ACHAT' else 'en dessous'} des EMA{config.EMA_FAST}/{config.EMA_SLOW} sur H1 (support/résistance)",
        raison_macd="MACD confirme le momentum sur M15",
        raison_tdi="TDI confirme le timing d'entrée sur M15",
        atr_valeur=atr_valeur,
    )
