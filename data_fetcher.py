"""
MR EMA - Récupération des données de marché via Yahoo Finance (yfinance)

IMPORTANT (transparence technique, à ne jamais retirer de ce fichier) :
Yahoo Finance fournit des données différées d'environ 15-20 minutes sur les comptes
gratuits, et impose des limites strictes sur l'historique disponible selon l'intervalle
(ex: le 15m est plafonné à ~60 jours peu importe la période demandée). Ce module ne
prétend jamais avoir plus de données que ce que Yahoo renvoie réellement : il vérifie
le nombre de bougies obtenu et écarte l'actif si l'historique est insuffisant pour un
calcul d'indicateur fiable, plutôt que de calculer sur des données tronquées.
"""

import time
import logging
import pandas as pd
import yfinance as yf

logger = logging.getLogger("mr_ema.data_fetcher")


class DonneesInsuffisantesError(Exception):
    """Levée quand Yahoo Finance ne renvoie pas assez de bougies pour un calcul fiable."""
    pass


def recuperer_bougies(ticker: str, interval: str, period: str, min_candles: int, max_retries: int = 3) -> pd.DataFrame:
    """
    Récupère les bougies OHLCV pour un ticker donné.

    Args:
        ticker: symbole yfinance (ex: "EURUSD=X", "BTC-USD")
        interval: "15m", "1h", "1d" etc.
        period: fenêtre demandée (ex: "60d") - Yahoo peut renvoyer moins selon ses limites
        min_candles: nombre minimum de bougies exigé pour considérer les données utilisables
        max_retries: nombre de tentatives en cas d'erreur réseau/API transitoire

    Returns:
        DataFrame pandas avec colonnes Open/High/Low/Close/Volume, index = timestamps

    Raises:
        DonneesInsuffisantesError: si Yahoo renvoie moins de bougies que min_candles
    """
    derniere_erreur = None

    for tentative in range(1, max_retries + 1):
        try:
            data = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)

            if data is None or data.empty:
                raise DonneesInsuffisantesError(
                    f"{ticker}: aucune donnée renvoyée par Yahoo Finance (interval={interval})"
                )

            # Nettoyage : on retire les lignes avec des NaN sur les prix (jours fériés, données manquantes)
            data = data.dropna(subset=["Open", "High", "Low", "Close"])

            if len(data) < min_candles:
                raise DonneesInsuffisantesError(
                    f"{ticker}: seulement {len(data)} bougies disponibles sur {interval} "
                    f"(minimum requis: {min_candles}). Actif ignoré ce cycle pour éviter "
                    f"un calcul d'indicateur non fiable."
                )

            logger.info(f"{ticker} [{interval}]: {len(data)} bougies récupérées avec succès")
            return data

        except DonneesInsuffisantesError:
            # Pas la peine de réessayer si le problème est le manque d'historique disponible
            raise

        except Exception as e:
            derniere_erreur = e
            logger.warning(f"{ticker} [{interval}]: tentative {tentative}/{max_retries} échouée ({e})")
            if tentative < max_retries:
                time.sleep(2 * tentative)  # backoff progressif

    # Toutes les tentatives réseau ont échoué
    raise DonneesInsuffisantesError(
        f"{ticker}: échec de récupération après {max_retries} tentatives ({derniere_erreur})"
    )


def recuperer_multi_timeframe(ticker: str, config_trend: dict, config_entry: dict,
                                min_candles_trend: int, min_candles_entry: int) -> dict:
    """
    Récupère les données sur les deux timeframes utilisés par la stratégie (H1 + M15).

    Returns:
        dict {"trend": DataFrame, "entry": DataFrame}
        ou None si l'un des deux timeframes n'a pas assez de données (actif ignoré ce cycle)
    """
    try:
        df_trend = recuperer_bougies(
            ticker,
            interval=config_trend["interval"],
            period=config_trend["period"],
            min_candles=min_candles_trend,
        )
        df_entry = recuperer_bougies(
            ticker,
            interval=config_entry["interval"],
            period=config_entry["period"],
            min_candles=min_candles_entry,
        )
        return {"trend": df_trend, "entry": df_entry}

    except DonneesInsuffisantesError as e:
        logger.warning(str(e))
        return None


def prix_actuel(ticker: str) -> float:
    """
    Récupère le dernier prix de clôture connu (rappel: différé de ~15-20 min sur Yahoo gratuit).
    Utilisé pour vérifier si un TP/SL a été touché.
    """
    data = recuperer_bougies(ticker, interval="15m", period="1d", min_candles=1)
    return float(data["Close"].iloc[-1])
