"""
MR EMA - Envoi des messages Telegram

Règles de formatage respectées ici :
- Le message de signal ne mentionne JAMAIS le nom de la stratégie ou des indicateurs utilisés
- Les valeurs de TP/SL sont mises en `code` (backticks Markdown) pour que Telegram les rende
  copiables d'un simple tap, sans sélection manuelle
- Chaque signal est obligatoirement accompagné d'une image (le graphique réel généré)
"""

import logging
import requests

import config

logger = logging.getLogger("mr_ema.telegram_sender")

API_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def _post(endpoint: str, data: dict = None, files: dict = None) -> dict:
    url = f"{API_BASE}/{endpoint}"
    try:
        response = requests.post(url, data=data, files=files, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Échec envoi Telegram ({endpoint}): {e}")
        return {"ok": False, "error": str(e)}


def envoyer_message(texte: str, parse_mode: str = "Markdown") -> dict:
    return _post("sendMessage", data={
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": texte,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    })


def envoyer_photo(chemin_image: str, legende: str = "", parse_mode: str = "Markdown") -> dict:
    with open(chemin_image, "rb") as photo:
        return _post("sendPhoto", data={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "caption": legende,
            "parse_mode": parse_mode,
        }, files={"photo": photo})


# ============================================================
# FORMATAGE DES MESSAGES
# ============================================================

def formater_message_signal(ticker_display: str, direction: str, niveaux, pips_risque: float,
                              pips_tp1: float, pips_tp2, pips_tp3) -> str:
    """
    Construit le message de signal. IMPORTANT : ne mentionne jamais la stratégie/les
    indicateurs utilisés en interne, conformément à la demande explicite du projet.
    Les valeurs numériques (entrée/SL/TP) sont en `code` pour être copiables sur Telegram.
    """
    emoji = "🟢 ACHAT" if direction == "ACHAT" else "🔴 VENTE"

    lignes = [
        f"{emoji} — *{ticker_display}*",
        "",
        f"Entrée : `{niveaux.prix_entree}`",
        f"Stop Loss : `{niveaux.stop_loss}` ({pips_risque} pips)",
        f"Take Profit 1 : `{niveaux.take_profit_1}` ({pips_tp1} pips — RR {niveaux.rr_tp1})",
    ]

    if niveaux.take_profit_2 is not None:
        lignes.append(f"Take Profit 2 : `{niveaux.take_profit_2}` ({pips_tp2} pips — RR {niveaux.rr_tp2})")
    if niveaux.take_profit_3 is not None:
        lignes.append(f"Take Profit 3 : `{niveaux.take_profit_3}` ({pips_tp3} pips — RR {niveaux.rr_tp3})")

    lignes += [
        "",
        "_Données de marché différées d'environ 15-20 min (Yahoo Finance)._",
        "_Ceci est un outil d'aide à la décision, pas un conseil financier._",
    ]

    return "\n".join(lignes)


def formater_message_evenement(position, evenement: str) -> str:
    """Message envoyé quand un TP ou le SL d'une position déjà ouverte est touché."""
    textes = {
        "TP1_TOUCHE": f"✅ *{position.ticker_display}* — Take Profit 1 touché (`{position.take_profit_1}`)",
        "TP2_TOUCHE": f"✅ *{position.ticker_display}* — Take Profit 2 touché (`{position.take_profit_2}`)",
        "TP3_TOUCHE": f"✅ *{position.ticker_display}* — Take Profit 3 touché (`{position.take_profit_3}`)",
        "SL_TOUCHE": f"❌ *{position.ticker_display}* — Stop Loss touché (`{position.stop_loss}`)",
    }
    base = textes.get(evenement, f"{position.ticker_display} — mise à jour: {evenement}")
    return base + "\n\n_Rappel : suivi basé sur des données différées de ~15-20 min._"


def formater_message_matin() -> str:
    return (
        "☀️ *Bonjour !*\n\n"
        "MR EMA est actif et surveille les marchés. "
        "Les signaux valides seront envoyés ici dès qu'un setup est confirmé.\n\n"
        "Bonne journée de trading 📊"
    )


def formater_bilan_soir(positions_fermees_du_jour: list, positions_encore_ouvertes: list) -> str:
    """Construit le bilan du soir (20h Burkina Faso) : gains/pertes de la journée."""
    if not positions_fermees_du_jour and not positions_encore_ouvertes:
        return (
            "🌙 *Bilan du soir*\n\n"
            "Aucune position ouverte ou clôturée aujourd'hui — le marché n'a pas offert "
            "de setup validé par la stratégie.\n\n"
            "_Rappel : mieux vaut aucun signal qu'un signal forcé._"
        )

    total_pips = sum(p.resultat_pips for p in positions_fermees_du_jour if p.resultat_pips is not None)
    gagnantes = [p for p in positions_fermees_du_jour if (p.resultat_pips or 0) > 0]
    perdantes = [p for p in positions_fermees_du_jour if (p.resultat_pips or 0) <= 0]

    lignes = ["🌙 *Bilan du soir*", ""]

    if positions_fermees_du_jour:
        lignes.append(f"Positions clôturées : {len(positions_fermees_du_jour)}")
        lignes.append(f"✅ Gagnantes : {len(gagnantes)}  |  ❌ Perdantes : {len(perdantes)}")
        signe = "+" if total_pips >= 0 else ""
        lignes.append(f"Résultat net (pips/USD selon actif) : {signe}{round(total_pips, 1)}")
        lignes.append("")

        for p in positions_fermees_du_jour:
            emoji = "✅" if (p.resultat_pips or 0) > 0 else "❌"
            signe_p = "+" if (p.resultat_pips or 0) >= 0 else ""
            lignes.append(f"{emoji} {p.ticker_display} ({p.direction}) : {signe_p}{round(p.resultat_pips or 0, 1)}")

    if positions_encore_ouvertes:
        lignes.append("")
        lignes.append(f"⏳ Positions encore ouvertes ce soir : {len(positions_encore_ouvertes)}")
        for p in positions_encore_ouvertes:
            lignes.append(f"— {p.ticker_display} ({p.direction}), ouverte à `{p.prix_entree}`")

    lignes += [
        "",
        "_Rappel : ces chiffres reflètent les niveaux techniques suivis par le robot, "
        "pas nécessairement l'exécution réelle sur ton compte de trading._",
    ]

    return "\n".join(lignes)
