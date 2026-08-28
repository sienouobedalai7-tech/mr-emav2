"""
================================================================================
MR EMA — Backtest de la stratégie sur données historiques réelles
================================================================================
Ce script réutilise DIRECTEMENT les modules du robot (strategy.py, indicators.py,
risk_management.py) — il ne réimplémente pas une version parallèle de la
stratégie. Le but est de tester exactement ce que le robot ferait en conditions
réelles, pas une approximation.

FONCTIONNEMENT :
Pour chaque actif, le script parcourt l'historique H1/M15 bougie par bougie
(comme si le temps s'écoulait réellement), appelle strategy.analyser_actif() à
chaque nouvelle bougie M15 exactement comme le ferait main.py en production,
et si un signal est généré, SIMULE son suivi bougie par bougie jusqu'à ce que
le SL, un TP, ou l'expiration de la position soit atteint — pour déterminer
si le trade aurait été gagnant ou perdant.

LANCEMENT (Pydroid3 ou tout environnement avec accès réseau) :
    pip install yfinance pandas numpy
    python3 backtest.py

RÉSULTAT : un rapport texte avec nombre de trades, win rate, profit factor,
RR moyen, détail par actif — affiché dans la console ET sauvegardé dans
data/rapport_backtest.txt

LIMITES HONNÊTES DE CE BACKTEST (à garder en tête en lisant les résultats) :
- Historique limité à ~60 jours en intraday (limite Yahoo Finance gratuite)
- Pas de slippage ni de spread simulés (les prix réels d'exécution seraient
  légèrement moins favorables)
- Suppose que chaque signal est tradable à l'instant exact de sa détection
- Un backtest positif ne garantit pas une performance future : il ne fait que
  raconter comment la stratégie se serait comportée sur cette période passée
================================================================================
"""

from __future__ import annotations
import sys
import time
import logging
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import numpy as np

import config
import indicators
import strategy
import risk_management
import data_fetcher

logging.basicConfig(level=logging.WARNING)  # on tait les logs internes du fetcher pendant le backtest
logger = logging.getLogger("mr_ema.backtest")

# Durée max d'une position en nombre de bougies M15, dérivée de la vraie
# constante du projet (config.MAX_POSITION_HOURS) pour rester cohérent avec le
# comportement réel du robot en production (day trading, clôture forcée après
# MAX_POSITION_HOURS heures). 1 heure = 4 bougies M15.
DUREE_MAX_BOUGIES_M15 = int(config.MAX_POSITION_HOURS * 4)


@dataclass
class TradeSimule:
    ticker: str
    direction: str
    prix_entree: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: Optional[float]
    rr_tp1: float
    resultat: str          # "TP1", "TP2", "SL", "EXPIRE"
    pips_resultat: float
    bougies_tenues: int


def _simuler_issue_trade(df_entry: pd.DataFrame, index_entree: int, niveaux: risk_management.NiveauxPosition,
                          asset_type: str, ticker_symbol: str) -> TradeSimule:
    """
    Simule le suivi d'un trade bougie par bougie après son entrée, jusqu'à ce
    que le SL, TP1, ou TP2 soit touché, ou que la durée max soit atteinte.

    Priorité en cas de bougie touchant SL et TP à la fois (mèche large) :
    on suppose le pire cas (SL touché en premier) par prudence — c'est plus
    réaliste qu'un optimisme injustifié sur l'ordre exact intra-bougie, qu'on
    ne peut pas connaître avec des données OHLC seules (pas de tick-by-tick).
    """
    direction = niveaux.direction
    fin_recherche = min(index_entree + 1 + DUREE_MAX_BOUGIES_M15, len(df_entry))

    for i in range(index_entree + 1, fin_recherche):
        bougie = df_entry.iloc[i]
        haut, bas = bougie["High"], bougie["Low"]

        if direction == "ACHAT":
            sl_touche = bas <= niveaux.stop_loss
            tp2_touche = niveaux.take_profit_2 is not None and haut >= niveaux.take_profit_2
            tp1_touche = haut >= niveaux.take_profit_1
        else:  # VENTE
            sl_touche = haut >= niveaux.stop_loss
            tp2_touche = niveaux.take_profit_2 is not None and bas <= niveaux.take_profit_2
            tp1_touche = bas <= niveaux.take_profit_1

        if sl_touche:
            pips = -abs(niveaux.pips_risque)
            return TradeSimule(ticker_symbol, direction, niveaux.prix_entree, niveaux.stop_loss,
                                niveaux.take_profit_1, niveaux.take_profit_2, niveaux.rr_tp1,
                                "SL", pips, i - index_entree)
        if tp2_touche:
            pips = abs(niveaux.pips_tp2)
            return TradeSimule(ticker_symbol, direction, niveaux.prix_entree, niveaux.stop_loss,
                                niveaux.take_profit_1, niveaux.take_profit_2, niveaux.rr_tp1,
                                "TP2", pips, i - index_entree)
        if tp1_touche:
            pips = abs(niveaux.pips_tp1)
            return TradeSimule(ticker_symbol, direction, niveaux.prix_entree, niveaux.stop_loss,
                                niveaux.take_profit_1, niveaux.take_profit_2, niveaux.rr_tp1,
                                "TP1", pips, i - index_entree)

    # Ni SL ni TP touché avant la durée max -> expiration, on compte le résultat flottant réel
    prix_final = df_entry.iloc[fin_recherche - 1]["Close"]
    pips_flottants = risk_management.calculer_pips(niveaux.prix_entree, prix_final, asset_type, ticker_symbol)
    if direction == "VENTE":
        pips_flottants = -pips_flottants
    return TradeSimule(ticker_symbol, direction, niveaux.prix_entree, niveaux.stop_loss,
                        niveaux.take_profit_1, niveaux.take_profit_2, niveaux.rr_tp1,
                        "EXPIRE", pips_flottants, fin_recherche - 1 - index_entree)


def backtester_actif(nom_actif: str, infos: dict, pas_verification: int = 4) -> list[TradeSimule]:
    """
    Backteste un actif sur tout l'historique disponible.

    pas_verification : on n'appelle pas strategy.analyser_actif() sur CHAQUE
    bougie M15 (trop lent et redondant vu que le TDI/MACD ne changent pas
    radicalement d'une bougie à l'autre) mais tous les `pas_verification`
    bougies — comportement représentatif d'un cron qui tourne toutes les
    10-15 min, proche d'une bougie M15.
    """
    ticker = infos["ticker"]
    trades: list[TradeSimule] = []

    try:
        df_trend_brut = data_fetcher.recuperer_bougies(
            ticker, config.TIMEFRAME_TREND["interval"], config.TIMEFRAME_TREND["period"],
            config.MIN_CANDLES_TREND,
        )
        time.sleep(1.5)  # pause entre les deux requêtes du même actif (rate limiting Yahoo Finance)
        df_entry_brut = data_fetcher.recuperer_bougies(
            ticker, config.TIMEFRAME_ENTRY["interval"], config.TIMEFRAME_ENTRY["period"],
            config.MIN_CANDLES_ENTRY,
        )
    except data_fetcher.DonneesInsuffisantesError as e:
        print(f"  [ignoré] {nom_actif}: {e}")
        return trades

    # On pré-calcule les indicateurs une seule fois sur tout l'historique (rapide),
    # puis on "rejoue" le temps en limitant les DataFrame vus par la stratégie à
    # ce qui aurait été disponible à cet instant précis (pas de fuite du futur).
    df_trend_ind = indicators.calculer_tous_indicateurs(
        df_trend_brut, config.EMA_FAST, config.EMA_SLOW, config.MACD_FAST, config.MACD_SLOW,
        config.MACD_SIGNAL, config.ATR_PERIOD, config.TDI_RSI_PERIOD, config.TDI_RSI_PRICE_LINE,
        config.TDI_TRADE_SIGNAL_LINE, config.TDI_VOLATILITY_BAND,
    )
    df_entry_ind = indicators.calculer_tous_indicateurs(
        df_entry_brut, config.EMA_FAST, config.EMA_SLOW, config.MACD_FAST, config.MACD_SLOW,
        config.MACD_SIGNAL, config.ATR_PERIOD, config.TDI_RSI_PERIOD, config.TDI_RSI_PRICE_LINE,
        config.TDI_TRADE_SIGNAL_LINE, config.TDI_VOLATILITY_BAND,
    )

    index_derniere_position_fermee = config.MIN_CANDLES_ENTRY  # on ne trade pas avant d'avoir assez d'historique

    for i in range(config.MIN_CANDLES_ENTRY, len(df_entry_ind), pas_verification):
        if i <= index_derniere_position_fermee:
            continue  # une position est déjà "ouverte" jusqu'à cet index dans la simulation

        # Fenêtre H1 correspondant à ce qui aurait été connu à l'instant de la bougie M15 n°i
        timestamp_courant = df_entry_ind.index[i]
        df_trend_visible = df_trend_ind[df_trend_ind.index <= timestamp_courant]
        df_entry_visible = df_entry_ind.iloc[:i + 1]

        if len(df_trend_visible) < config.MIN_CANDLES_TREND:
            continue

        try:
            signal = strategy.analyser_actif(ticker, infos["type"], df_trend_visible, df_entry_visible)
        except Exception as e:  # noqa: BLE001 - un actif en erreur ne doit jamais arrêter tout le backtest
            print(f"  [erreur analyse] {nom_actif} à l'index {i}: {e}")
            continue

        if signal is None:
            continue

        trade = _simuler_issue_trade(df_entry_ind, i, signal.niveaux, infos["type"], ticker)
        trades.append(trade)
        index_derniere_position_fermee = i + trade.bougies_tenues

    return trades


def generer_rapport(tous_les_trades: list[TradeSimule]) -> str:
    lignes = []
    lignes.append("=" * 70)
    lignes.append("MR EMA — RAPPORT DE BACKTEST")
    lignes.append("=" * 70)
    lignes.append("")

    n_total = len(tous_les_trades)
    if n_total == 0:
        lignes.append("Aucun trade généré sur la période testée.")
        return "\n".join(lignes)

    gagnants = [t for t in tous_les_trades if t.pips_resultat > 0]
    perdants = [t for t in tous_les_trades if t.pips_resultat <= 0]

    win_rate = len(gagnants) / n_total * 100
    gain_total = sum(t.pips_resultat for t in gagnants)
    perte_totale = abs(sum(t.pips_resultat for t in perdants))
    profit_factor = (gain_total / perte_totale) if perte_totale > 0 else float("inf")
    pips_net = sum(t.pips_resultat for t in tous_les_trades)
    rr_moyen = np.mean([t.rr_tp1 for t in tous_les_trades])

    repartition = {}
    for t in tous_les_trades:
        repartition[t.resultat] = repartition.get(t.resultat, 0) + 1

    lignes.append(f"Nombre total de trades      : {n_total}")
    lignes.append(f"Trades gagnants              : {len(gagnants)} ({win_rate:.1f}%)")
    lignes.append(f"Trades perdants               : {len(perdants)} ({100 - win_rate:.1f}%)")
    lignes.append(f"Profit factor                 : {profit_factor:.2f}" +
                   ("  (> 1.0 = rentable sur cette période, < 1.0 = perdant)" if profit_factor != float('inf') else ""))
    lignes.append(f"Pips nets cumulés (approx.)    : {pips_net:+.1f}")
    lignes.append(f"RR moyen visé (TP1)            : {rr_moyen:.2f}")
    lignes.append("")
    lignes.append("Répartition des issues :")
    for issue, count in sorted(repartition.items()):
        lignes.append(f"  {issue:8s} : {count} ({count/n_total*100:.1f}%)")
    lignes.append("")

    lignes.append("-" * 70)
    lignes.append("Détail par actif :")
    lignes.append("-" * 70)
    par_actif: dict[str, list[TradeSimule]] = {}
    for t in tous_les_trades:
        par_actif.setdefault(t.ticker, []).append(t)

    for ticker, trades_actif in sorted(par_actif.items()):
        n = len(trades_actif)
        gagnants_actif = sum(1 for t in trades_actif if t.pips_resultat > 0)
        pips_actif = sum(t.pips_resultat for t in trades_actif)
        lignes.append(f"  {ticker:12s} : {n:3d} trades | {gagnants_actif}/{n} gagnants | {pips_actif:+.1f} pips")

    lignes.append("")
    lignes.append("=" * 70)
    lignes.append("RAPPEL IMPORTANT :")
    lignes.append("Ce backtest ne simule ni le spread ni le slippage (l'exécution réelle")
    lignes.append("serait légèrement moins favorable). L'historique Yahoo Finance gratuit")
    lignes.append("est limité à ~60 jours en intraday : un résultat positif ici ne garantit")
    lignes.append("PAS une performance future, il décrit seulement le passé récent testé.")
    lignes.append("=" * 70)

    return "\n".join(lignes)


def main():
    print(f"Démarrage du backtest sur {len(config.ASSETS)} actifs...")
    print("(Ceci peut prendre plusieurs minutes selon la connexion réseau.)\n")

    tous_les_trades: list[TradeSimule] = []

    for i, (nom_actif, infos) in enumerate(config.ASSETS.items(), 1):
        print(f"[{i}/{len(config.ASSETS)}] Backtest en cours : {nom_actif} ({infos['ticker']})...")
        trades = backtester_actif(nom_actif, infos)
        print(f"  -> {len(trades)} trade(s) généré(s)")
        tous_les_trades.extend(trades)
        time.sleep(3)  # pause plus longue entre chaque actif pour éviter le rate limiting
                        # Yahoo Finance (erreur 429 "Too Many Requests") qui casse tout
                        # le backtest si les requêtes s'enchaînent trop vite

    rapport = generer_rapport(tous_les_trades)
    print("\n" + rapport)

    chemin_rapport = "data/rapport_backtest.txt"
    with open(chemin_rapport, "w", encoding="utf-8") as f:
        f.write(rapport)
    print(f"\nRapport sauvegardé dans : {chemin_rapport}")


if __name__ == "__main__":
    main()
