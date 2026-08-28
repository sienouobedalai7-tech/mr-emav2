"""
MR EMA - Configuration centrale du robot
Tous les paramètres modifiables sont ici, rien n'est en dur ailleurs dans le code.
"""

import os

# Charge automatiquement un fichier .env local s'il existe (pratique sur VPS
# pour éviter de repasser les variables d'environnement à chaque lancement).
# Le fichier .env n'est JAMAIS commité dans Git (voir .gitignore) — chacun
# crée le sien localement à partir de .env.example.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv non installé : on utilise alors les vraies variables d'environnement système

# ============================================================
# TELEGRAM
# ============================================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")  # ID du canal/groupe, pas l'URL

# ============================================================
# ACTIFS SUIVIS
# ============================================================
# Format yfinance :
#   Forex   -> "EURUSD=X"
#   Métaux  -> "XAUUSD=X" (or), "XAGUSD=X" (argent)
#   Crypto  -> "BTC-USD"
#
# NB: XAU/USD et XAG/USD passent par le même suffixe =X que le forex sur Yahoo Finance.

ASSETS = {
    # --- Demandés explicitement ---
    "XAUUSD": {"ticker": "XAUUSD=X", "type": "metal", "display": "XAU/USD (Or)"},
    "XAGUSD": {"ticker": "XAGUSD=X", "type": "metal", "display": "XAG/USD (Argent)"},
    "BTCUSD": {"ticker": "BTC-USD", "type": "crypto", "display": "BTC/USD"},
    "GBPUSD": {"ticker": "GBPUSD=X", "type": "forex", "display": "GBP/USD"},

    # --- 20 paires Forex parmi les plus volatiles/liquides (majeures + croisées connues pour leur volatilité) ---
    "EURUSD": {"ticker": "EURUSD=X", "type": "forex", "display": "EUR/USD"},
    "USDJPY": {"ticker": "USDJPY=X", "type": "forex", "display": "USD/JPY"},
    "USDCHF": {"ticker": "USDCHF=X", "type": "forex", "display": "USD/CHF"},
    "AUDUSD": {"ticker": "AUDUSD=X", "type": "forex", "display": "AUD/USD"},
    "USDCAD": {"ticker": "USDCAD=X", "type": "forex", "display": "USD/CAD"},
    "NZDUSD": {"ticker": "NZDUSD=X", "type": "forex", "display": "NZD/USD"},
    "EURJPY": {"ticker": "EURJPY=X", "type": "forex", "display": "EUR/JPY"},
    "GBPJPY": {"ticker": "GBPJPY=X", "type": "forex", "display": "GBP/JPY"},
    "EURGBP": {"ticker": "EURGBP=X", "type": "forex", "display": "EUR/GBP"},
    "AUDJPY": {"ticker": "AUDJPY=X", "type": "forex", "display": "AUD/JPY"},
    "EURAUD": {"ticker": "EURAUD=X", "type": "forex", "display": "EUR/AUD"},
    "GBPAUD": {"ticker": "GBPAUD=X", "type": "forex", "display": "GBP/AUD"},
    "GBPCAD": {"ticker": "GBPCAD=X", "type": "forex", "display": "GBP/CAD"},
    "EURCAD": {"ticker": "EURCAD=X", "type": "forex", "display": "EUR/CAD"},
    "AUDCAD": {"ticker": "AUDCAD=X", "type": "forex", "display": "AUD/CAD"},
    "AUDNZD": {"ticker": "AUDNZD=X", "type": "forex", "display": "AUD/NZD"},
    "CADJPY": {"ticker": "CADJPY=X", "type": "forex", "display": "CAD/JPY"},
    "CHFJPY": {"ticker": "CHFJPY=X", "type": "forex", "display": "CHF/JPY"},
    "NZDJPY": {"ticker": "NZDJPY=X", "type": "forex", "display": "NZD/JPY"},
    "EURCHF": {"ticker": "EURCHF=X", "type": "forex", "display": "EUR/CHF"},
}

# ============================================================
# TIMEFRAMES (Multi-Timeframe Analysis)
# ============================================================
# H1 = filtre de tendance (EMA50/200 stables)
# M15 = timing d'entrée (MACD + TDI)
# Yahoo Finance limite le nombre de bougies réellement disponible selon l'intervalle,
# donc on demande 1000 mais le code s'adapte si Yahoo en renvoie moins (voir data_fetcher.py).
TIMEFRAME_TREND = {"interval": "1h", "period": "60d", "candles_target": 1000}
TIMEFRAME_ENTRY = {"interval": "15m", "period": "60d", "candles_target": 1000}

# Nombre minimum de bougies pour qu'un calcul d'indicateur soit jugé fiable.
# En dessous de ce seuil sur un actif donné, l'actif est ignoré ce cycle (pas de faux signal
# basé sur un EMA200 mal formé).
MIN_CANDLES_TREND = 210   # marge au-dessus de 200 pour un EMA200 stable
MIN_CANDLES_ENTRY = 50

# ============================================================
# PARAMÈTRES DES INDICATEURS
# ============================================================
EMA_FAST = 50
EMA_SLOW = 200

MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

ATR_PERIOD = 14

# TDI (Traders Dynamic Index) - paramètres standards
TDI_RSI_PERIOD = 13
TDI_RSI_PRICE_LINE = 2       # lissage de la ligne RSI (ligne verte)
TDI_TRADE_SIGNAL_LINE = 7    # ligne de signal (ligne rouge)
TDI_VOLATILITY_BAND = 34     # bandes de Bollinger sur le RSI

# ============================================================
# RISK MANAGEMENT (règle non-négociable du projet)
# ============================================================
# Ratio Risque:Récompense autorisé - AUCUN signal en dehors de cet intervalle n'est envoyé.
MIN_RISK_REWARD = 1.60
MAX_RISK_REWARD = 3.20

# Multiplicateurs ATR pour construire SL / TP (ajustés puis validés contre le RR autorisé)
ATR_SL_MULTIPLIER = 1.5      # Stop Loss = distance en ATR par rapport à l'entrée
ATR_TP1_MULTIPLIER = 2.4     # TP1 ≈ RR 1.60 (2.4/1.5)
ATR_TP2_MULTIPLIER = 3.6     # TP2 ≈ RR 2.40 (3.6/1.5)
ATR_TP3_MULTIPLIER = 4.8     # TP3 ≈ RR 3.20 (4.8/1.5) - plafond autorisé

# ============================================================
# HORAIRES (fuseau Burkina Faso = UTC+0, pas de changement d'heure d'été)
# ============================================================
TIMEZONE_BF = "Africa/Ouagadougou"  # UTC+0 toute l'année
MORNING_HOUR_BF = 7
EVENING_HOUR_BF = 20

# ============================================================
# FICHIERS D'ÉTAT (persistance entre les runs cron)
# ============================================================
STATE_FILE = "data/positions_ouvertes.json"
HISTORY_FILE = "data/historique_cloture.json"

# ============================================================
# LIMITE ANTI-SPAM
# ============================================================
# Nombre maximum de nouveaux signaux envoyés sur Telegram en un seul cycle
# d'analyse. Protège contre le spam si plusieurs actifs corrélés valident un
# setup simultanément. Les actifs non traités sont réévalués au cycle suivant.
MAX_SIGNAUX_PAR_CYCLE = 3

# ============================================================
# DAY TRADING - Durée de vie maximale d'une position
# ============================================================
# Le robot fait du day trading : une position ne doit jamais rester ouverte
# au-delà de cette durée. Si dépassée, elle est clôturée automatiquement au marché.
MAX_POSITION_HOURS = 18  # ouverte le matin, fermée avant le lendemain matin max
