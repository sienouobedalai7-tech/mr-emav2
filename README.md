# MR EMA v2 — Robot d'analyse de marché avec alertes Telegram

## ⚠️ À lire avant de déployer

1. **Ce robot n'exécute aucun trade.** Il analyse le marché et envoie des alertes sur Telegram. Toute décision de trading reste la tienne.
2. **Les données Yahoo Finance sont différées d'environ 15-20 minutes.** Ce n'est pas du vrai temps réel — c'est une limite technique de la source de données gratuite, pas un défaut du code.
3. **Aucune stratégie technique ne garantit un gain.** EMA (support/résistance H1) + MACD + TDI + ATR forment une architecture cohérente (tendance + momentum + timing + risque), mais ils sont dérivés du même prix historique et ne prédisent rien. Teste en observation avant tout capital réel — voir la section Backtest plus bas.
4. **Régénère ton token Telegram** si tu l'as déjà partagé ailleurs qu'ici (via [@BotFather](https://t.me/BotFather), commande `/revoke`) avant de le mettre dans GitHub Secrets.

## Étape 1 — Créer le repo GitHub

1. Va sur [github.com/new](https://github.com/new)
2. Nom du repo : `mr-ema-v2` (ou ce que tu veux)
3. **Visibilité : Public** obligatoire sur compte gratuit pour que le cron `schedule` de GitHub Actions se déclenche automatiquement (les repos privés nécessitent un plan payant pour les crons programmés — voir la section dédiée plus bas)
4. Crée le repo, puis upload tous les fichiers de ce projet en conservant la structure des dossiers (en particulier `.github/workflows/` doit rester à cet emplacement exact, avec ses deux fichiers `trading-bot.yml` et `backtest.yml`)

## Étape 2 — Configurer les secrets

Dans le repo : **Settings → Secrets and variables → Actions → New repository secret**

Ajoute ces deux secrets :

| Nom | Valeur |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Le token de ton bot (donné par BotFather) |
| `TELEGRAM_CHAT_ID` | L'ID de ton canal Telegram (voir ci-dessous) |

### Trouver ton `TELEGRAM_CHAT_ID`

1. Ajoute ton bot comme administrateur du canal `https://t.me/mrema26382`
2. Poste n'importe quel message dans le canal
3. Ouvre dans un navigateur : `https://api.telegram.org/bot<TON_TOKEN>/getUpdates`
4. Cherche `"chat":{"id":-100xxxxxxxxxx` dans la réponse — ce nombre (négatif, commence souvent par `-100` pour un canal) est ton `TELEGRAM_CHAT_ID`

## Étape 3 — Activer le workflow

1. Onglet **Actions** du repo
2. Si demandé, clique sur "I understand my workflows, go ahead and enable them"
3. Sélectionne le workflow "MR EMA - Robot de Trading"
4. Clique sur **Run workflow** pour un premier test manuel (bouton en haut à droite)
5. Vérifie les logs : si tout est vert, le robot est opérationnel. Le message ou l'erreur apparaît dans "run-robot" → "Lancer le robot MR EMA"

Une fois validé, le cron tourne automatiquement toutes les 10 minutes — plus rien à faire.

## Structure du projet

```
mr-ema-v2/
├── .github/workflows/
│   ├── trading-bot.yml   ← cron 10 min : analyse le marché et envoie les signaux
│   └── backtest.yml       ← déclenchement manuel uniquement : teste la stratégie sur l'historique
├── config.py              ← tous les paramètres (actifs, indicateurs, risk management)
├── data_fetcher.py         ← récupération des données Yahoo Finance
├── indicators.py           ← calcul EMA / MACD / ATR / TDI
├── risk_management.py      ← calcul des pips + validation stricte du RR [1.60, 3.20]
├── strategy.py              ← logique multi-timeframe (H1 tendance + M15 entrée)
├── position_manager.py      ← suivi des positions ouvertes (TP/SL touchés)
├── telegram_sender.py        ← formatage et envoi des messages Telegram
├── chart_generator.py        ← génération des graphiques envoyés avec chaque signal
├── main.py                    ← point d'entrée, exécuté par le cron
├── backtest.py                 ← simulation de la stratégie sur données historiques réelles
├── requirements.txt
└── data/                        ← état persistant (committé automatiquement par le cron)
    ├── positions_ouvertes.json
    └── historique_cloture.json
```

## Ce que fait le robot à chaque cycle (10 min)

1. Relit les positions ouvertes depuis `data/positions_ouvertes.json`
2. Vérifie si un TP ou le SL de chaque position a été touché → notifie sur Telegram
3. Clôture automatiquement toute position dépassant 18h (règle day-trading stricte)
4. Analyse les 24 actifs configurés (Forex, XAU/USD, XAG/USD, BTC/USD) avec la stratégie complète
5. Envoie un signal (texte + graphique) pour chaque setup où EMA (H1) + MACD (M15) + TDI (M15) sont alignés ET où le ratio risque:récompense tombe dans [1.60, 3.20]
6. À 7h Burkina Faso : message de bonjour. À 20h : bilan de la journée
7. Sauvegarde l'état mis à jour (commit automatique dans `data/`)

## Modifier les actifs suivis ou les paramètres

Tout se passe dans `config.py` :
- `ASSETS` : ajouter/retirer des actifs (format ticker Yahoo Finance)
- `MIN_RISK_REWARD` / `MAX_RISK_REWARD` : la fourchette RR autorisée (actuellement 1.60-3.20, non-négociable dans le projet initial)
- `EMA_FAST` / `EMA_SLOW` / paramètres MACD / TDI / ATR : réglages des indicateurs
- `MORNING_HOUR_BF` / `EVENING_HOUR_BF` : horaires des messages programmés
- `MAX_POSITION_HOURS` : durée max avant clôture automatique (day trading)

## Lancer un backtest sur GitHub Actions

Plutôt que d'installer `yfinance` sur Pydroid3 (compilation de dépendances
souvent problématique sur Android), le backtest peut tourner directement sur
les serveurs GitHub, qui ont un environnement Linux propre et un accès
réseau fiable :

1. Onglet **Actions** du repo
2. Sélectionne le workflow **"MR EMA v2 - Backtest (manuel)"**
3. **Run workflow** → confirme
4. Compte plusieurs minutes (24 actifs × 2 timeframes, avec des pauses volontaires pour éviter le rate-limiting Yahoo Finance)
5. Une fois le run terminé (coche verte), clique dessus → en bas de la page, section **Artifacts** → télécharge **rapport-backtest**
6. Le fichier `.txt` contient le rapport complet : nombre de trades, win rate, profit factor, détail par actif

Ce workflow ne tourne jamais tout seul (pas de cron) — uniquement quand tu le déclenches manuellement.

## Repo privé et cron automatique : la limite à connaître

Sur un compte GitHub gratuit, les workflows `schedule` (cron automatique) ne
se déclenchent **que sur les repos publics**. C'est pour ça que ce guide
recommande la visibilité Public à l'étape 1. Si tu préfères un repo privé
pour ce projet, le cron `trading-bot.yml` ne se déclenchera jamais tout
seul — tu devras soit passer sur un plan GitHub payant, soit lancer le
robot manuellement via `workflow_dispatch` à chaque fois, soit héberger le
robot ailleurs (VPS, par exemple).



- **Délai Yahoo Finance** : ~15-20 min. Le cron lui-même peut avoir 5-15 min de retard aux heures de pointe GitHub Actions.
- **Historique limité en M15** : Yahoo Finance plafonne à ~60 jours sur cet intervalle, peu importe la période demandée dans le code.
- **`workflow_dispatch`** permet de lancer le robot manuellement depuis l'onglet Actions pour tester sans attendre le prochain cycle cron.
