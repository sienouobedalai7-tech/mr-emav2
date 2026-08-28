"""
MR EMA - Point d'entrée principal

Ce script est exécuté par le cron GitHub Actions toutes les 10 minutes.
À chaque exécution, il :
  1. Recharge l'état des positions ouvertes (fichier JSON committé dans le repo)
  2. Vérifie si les positions ouvertes ont touché un TP ou leur SL -> notifie Telegram
  3. Vérifie si des positions day-trading ont dépassé la durée max autorisée -> les clôture
  4. Analyse tous les actifs de config.ASSETS avec la stratégie complète
  5. Envoie un signal Telegram (texte + graphique) pour chaque setup validé
  6. Si l'heure correspond à 7h ou 20h (Burkina Faso) : envoie le message programmé correspondant
  7. Sauvegarde l'état mis à jour (relu par le prochain run du cron)
"""

import os
import logging
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

import config
import data_fetcher
import strategy
import risk_management
import position_manager
import telegram_sender
import chart_generator
import indicators

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mr_ema.main")

DOSSIER_GRAPHIQUES_TEMP = "data/graphiques_temp"


def _heure_actuelle_burkina() -> datetime:
    return datetime.now(ZoneInfo(config.TIMEZONE_BF))


def _deja_envoye_aujourdhui(nom_evenement: str) -> bool:
    """
    Évite d'envoyer deux fois le message du matin ou du soir si le cron tourne plusieurs
    fois dans la même fenêtre de 10 min où l'heure cible tombe. Utilise un petit fichier
    marqueur horodaté à la date du jour.
    """
    chemin = f"data/marqueurs_{nom_evenement}.txt"
    aujourdhui = date.today().isoformat()

    if os.path.exists(chemin):
        with open(chemin, "r") as f:
            if f.read().strip() == aujourdhui:
                return True

    os.makedirs("data", exist_ok=True)
    with open(chemin, "w") as f:
        f.write(aujourdhui)
    return False


def traiter_messages_programmes(positions_ouvertes: list) -> None:
    """Envoie le message du matin (7h) ou le bilan du soir (20h), heure Burkina Faso."""
    maintenant_bf = _heure_actuelle_burkina()

    # Fenêtre de tolérance de 10 min (durée du cron) autour de l'heure cible
    if maintenant_bf.hour == config.MORNING_HOUR_BF and not _deja_envoye_aujourdhui("matin"):
        logger.info("Envoi du message du matin")
        telegram_sender.envoyer_message(telegram_sender.formater_message_matin())

    if maintenant_bf.hour == config.EVENING_HOUR_BF and not _deja_envoye_aujourdhui("soir"):
        logger.info("Envoi du bilan du soir")
        historique = position_manager.charger_historique()
        aujourdhui_str = date.today().isoformat()
        fermees_aujourdhui = [
            position_manager.Position(**p) for p in historique
            if p.get("fermee_le", "").startswith(aujourdhui_str)
        ]
        telegram_sender.envoyer_message(
            telegram_sender.formater_bilan_soir(fermees_aujourdhui, positions_ouvertes)
        )


def suivre_positions_ouvertes(positions: list) -> list:
    """Vérifie chaque position ouverte : TP/SL touché, ou expiration day-trading."""
    positions_encore_ouvertes = []

    for position in positions:
        try:
            prix = data_fetcher.prix_actuel(position.ticker)
        except data_fetcher.DonneesInsuffisantesError as e:
            logger.warning(f"Impossible de vérifier {position.ticker}: {e}")
            positions_encore_ouvertes.append(position)
            continue

        # Expiration day-trading : priorité sur la vérification TP/SL normale
        if position_manager.verifier_expiration_day_trading(position):
            position.statut = "FERMEE_EXPIREE"
            position.resultat_pips = risk_management.calculer_pips(
                position.prix_entree, prix, position.asset_type, position.ticker
            )
            position.fermee_le = datetime.now(timezone.utc).isoformat()
            position_manager.ajouter_a_historique(position)
            telegram_sender.envoyer_message(
                f"⏰ *{position.ticker_display}* — Position clôturée (durée max day-trading atteinte)\n"
                f"Résultat : {position.resultat_pips} pips"
            )
            continue

        position, evenements = position_manager.verifier_position(position, prix)

        for evenement in evenements:
            telegram_sender.envoyer_message(telegram_sender.formater_message_evenement(position, evenement))

        if position.statut in ("FERMEE_TP", "FERMEE_SL"):
            position_manager.ajouter_a_historique(position)
        else:
            positions_encore_ouvertes.append(position)

    return positions_encore_ouvertes


def analyser_et_signaler(positions_ouvertes: list) -> list:
    """
    Analyse tous les actifs, envoie les signaux validés, retourne les nouvelles positions.

    Limite volontaire : au maximum config.MAX_SIGNAUX_PAR_CYCLE nouveaux signaux sont
    envoyés en un seul cycle, pour éviter de spammer le canal Telegram si plusieurs
    actifs valident un setup au même moment (ex: un mouvement de marché large qui
    déclenche 8-10 actifs corrélés simultanément). Les actifs non traités ce cycle
    seront réévalués au cycle suivant — aucun signal n'est "perdu", juste étalé dans
    le temps. La stratégie elle-même (strategy.py) n'est pas modifiée par cette limite.
    """
    nouvelles_positions = []
    tickers_deja_ouverts = {p.ticker for p in positions_ouvertes}
    signaux_envoyes_ce_cycle = 0

    os.makedirs(DOSSIER_GRAPHIQUES_TEMP, exist_ok=True)

    for nom_actif, infos in config.ASSETS.items():
        if signaux_envoyes_ce_cycle >= config.MAX_SIGNAUX_PAR_CYCLE:
            logger.info(
                f"Limite de {config.MAX_SIGNAUX_PAR_CYCLE} signaux atteinte pour ce cycle "
                f"— actifs restants réévalués au prochain cycle."
            )
            break

        ticker = infos["ticker"]

        # On évite d'empiler plusieurs positions sur le même actif en même temps
        if ticker in tickers_deja_ouverts:
            continue

        donnees = data_fetcher.recuperer_multi_timeframe(
            ticker,
            config.TIMEFRAME_TREND,
            config.TIMEFRAME_ENTRY,
            config.MIN_CANDLES_TREND,
            config.MIN_CANDLES_ENTRY,
        )

        if donnees is None:
            continue  # actif ignoré ce cycle (historique insuffisant), déjà loggé par data_fetcher

        try:
            signal = strategy.analyser_actif(ticker, infos["type"], donnees["trend"], donnees["entry"])
        except Exception as e:
            logger.error(f"Erreur d'analyse sur {ticker}: {e}")
            continue

        if signal is None:
            continue  # pas de setup validé sur cet actif ce cycle, comportement normal

        # --- Signal validé : construction du message + graphique + envoi ---
        logger.info(f"Signal validé sur {ticker}: {signal.direction} (RR TP1={signal.niveaux.rr_tp1})")

        position = position_manager.ouvrir_position(ticker, infos["display"], infos["type"], signal)
        nouvelles_positions.append(position)
        signaux_envoyes_ce_cycle += 1

        message = telegram_sender.formater_message_signal(
            infos["display"], signal.direction, signal.niveaux,
            signal.niveaux.pips_risque, signal.niveaux.pips_tp1,
            signal.niveaux.pips_tp2, signal.niveaux.pips_tp3,
        )

        df_trend_avec_indicateurs = indicators.calculer_tous_indicateurs(
            donnees["trend"], config.EMA_FAST, config.EMA_SLOW,
            config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL,
            config.ATR_PERIOD, config.TDI_RSI_PERIOD, config.TDI_RSI_PRICE_LINE,
            config.TDI_TRADE_SIGNAL_LINE, config.TDI_VOLATILITY_BAND,
        )
        df_entry_avec_indicateurs = indicators.calculer_tous_indicateurs(
            donnees["entry"], config.EMA_FAST, config.EMA_SLOW,
            config.MACD_FAST, config.MACD_SLOW, config.MACD_SIGNAL,
            config.ATR_PERIOD, config.TDI_RSI_PERIOD, config.TDI_RSI_PRICE_LINE,
            config.TDI_TRADE_SIGNAL_LINE, config.TDI_VOLATILITY_BAND,
        )

        take_profits = [signal.niveaux.take_profit_1, signal.niveaux.take_profit_2, signal.niveaux.take_profit_3]
        chemin_image = f"{DOSSIER_GRAPHIQUES_TEMP}/{nom_actif}.png"

        try:
            chart_generator.generer_graphique(
                df_entry_avec_indicateurs, infos["display"], signal.direction,
                signal.niveaux.prix_entree, signal.niveaux.stop_loss, take_profits, chemin_image,
                ema_fast_h1=df_trend_avec_indicateurs["ema_fast"],
                ema_slow_h1=df_trend_avec_indicateurs["ema_slow"],
            )
            telegram_sender.envoyer_photo(chemin_image, legende=message)
        except Exception as e:
            logger.error(f"Échec génération/envoi du graphique pour {ticker}: {e} - envoi du texte seul")
            telegram_sender.envoyer_message(message)

    return nouvelles_positions


def main():
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant dans les variables d'environnement")
        return

    logger.info("=== MR EMA - Démarrage du cycle d'analyse ===")

    positions_ouvertes = position_manager.charger_positions_ouvertes()
    logger.info(f"{len(positions_ouvertes)} position(s) actuellement ouverte(s)")

    positions_ouvertes = suivre_positions_ouvertes(positions_ouvertes)

    nouvelles_positions = analyser_et_signaler(positions_ouvertes)
    positions_ouvertes.extend(nouvelles_positions)

    traiter_messages_programmes(positions_ouvertes)

    position_manager.sauvegarder_positions_ouvertes(positions_ouvertes)

    logger.info(f"=== Cycle terminé - {len(positions_ouvertes)} position(s) ouverte(s) au total ===")


if __name__ == "__main__":
    main()
