"""
Kicktipp WorldPrediction2026 — Telegram Bot
/leaderboard — prediction matrix for match scores
/bonus       — bonus question predictions matrix
"""

import os
import re
import logging
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
BASE_URL  = "https://www.kicktipp.com/worldprediction2026/leaderboard"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.kicktipp.com/",
}

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Shared helpers ────────────────────────────────────────────────────────────

def get_soup(params=None):
    r = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def split_pred(raw):
    """
    Kicktipp concatenates prediction + points: '2-09' → pred='2-0', pts='9'
    Away score is always 1 digit; extra digits are points.
    """
    raw = raw.strip()
    if not raw or raw == "---":
        return "·", ""
    m = re.match(r'^(\d+)-(\d+)$', raw)
    if m:
        home, away_pts = m.group(1), m.group(2)
        if len(away_pts) > 1:
            return f"{home}-{away_pts[0]}", away_pts[1:]
        return f"{home}-{away_pts}", ""
    return raw, ""


def split_bonus_pred(raw):
    """
    Bonus predictions are team abbrs sometimes with points appended:
    'MEX10' → pred='MEX', pts='10'
    'ARG'   → pred='ARG', pts=''
    'USA10' → pred='USA', pts='10'
    'GER10' → pred='GER', pts='10'
    """
    raw = raw.strip()
    if not raw or raw == "---":
        return "·", ""
    m = re.match(r'^([A-Z]+)(\d+)?$', raw)
    if m:
        return m.group(1), m.group(2) or ""
    return raw, ""


# ── Match leaderboard ─────────────────────────────────────────────────────────

def parse_header_cell(cell):
    """'MEXRSA2-0' → ('MEX RSA', '2-0'),  'QATCH---' → ('QAT CH', '---')"""
    m = re.match(r'^([A-Z]+)([\d]+-[\d]+|---)$', cell)
    if not m:
        return None, None
    teams, result = m.group(1), m.group(2)
    t1, t2 = teams[:3], teams[3:]
    if not t2:
        return None, None
    return f"{t1} {t2}", result


def fetch_matrix():
    soup = get_soup()
    match_labels, match_results, players = [], [], []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        header_row, num_matches = None, 0

        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            labels, results = [], []
            for cell in cells:
                label, result = parse_header_cell(cell)
                if label:
                    labels.append(label)
                    results.append(result)
            if labels:
                match_labels, match_results = labels, results
                num_matches, header_row = len(labels), row
                break

        if not header_row:
            continue

        for row in rows:
            if row is header_row:
                continue
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) < 4:
                continue
            pos = cells[0].replace(".", "").strip()
            if not pos.isdigit():
                continue
            name = cells[2].strip()
            if not name:
                continue

            preds, pts_list = [], []
            for i in range(num_matches):
                col = 3 + i
                raw = cells[col] if col < len(cells) else ""
                pred, pts = split_pred(raw)
                preds.append(pred)
                pts_list.append(pts)

            total = cells[-1].strip() or "0"
            players.append({"pos": int(pos), "name": name, "preds": preds, "total": total})

        if players:
            break

    return match_labels, match_results, players


def build_match_table(match_labels, match_results, players):
    if not match_labels or not players:
        return "⚠️ No data found. Try again later."

    name_w = 7
    col_w  = 3
    home_teams = [lbl.split()[0][:3] for lbl in match_labels]
    away_teams = [lbl.split()[1][:3] if len(lbl.split()) > 1 else "   " for lbl in match_labels]

    def fmt_result(r):
        return "·" if r in ("---", "-", "") else r

    def make_row(name_col, pred_cols, tot_col):
        return (
            name_col[:name_w].ljust(name_w) + " " +
            " ".join(c.center(col_w) for c in pred_cols) +
            f" {tot_col:>2}"
        )

    header1 = make_row("",      home_teams,                       " ")
    header2 = make_row("",      away_teams,                       "T")
    score_r = make_row("Res",   [fmt_result(r) for r in match_results], " ")
    divider = "-" * len(header1)

    lines = ["🏆 *WorldPrediction2026*\n", "```"]
    lines.append(header1)
    lines.append(header2)
    lines.append(score_r)
    lines.append(divider)
    for p in players:
        preds = [p["preds"][i] if i < len(p["preds"]) else "·" for i in range(len(match_labels))]
        lines.append(make_row(p["name"], preds, p["total"]))
    lines.append("```")
    lines.append("_· = no prediction yet · T = total pts_")
    return "\n".join(lines)


# ── Bonus leaderboard ─────────────────────────────────────────────────────────

SKIP_COLS = {'Pos', '+/-', 'Name', 'P', 'B', 'W', 'T'}

def parse_bonus_header(cell):
    """
    Real bonus header cells: 'WC ---', 'Tor ---', 'Gr A MEX', 'Gr D USA', 'SF ---'
    Split: everything before last word = abbr, last word = result.
    """
    cell = cell.strip()
    m = re.match(r'^(.+?)\s+(---|\w{2,})$', cell)
    if not m:
        return None, None
    abbr   = m.group(1).strip()
    result = m.group(2).strip()
    if abbr in SKIP_COLS:
        return None, None
    if result == "---":
        result = "·"
    return abbr, result


def fetch_bonus():
    soup = get_soup(params={"tippsaisonId": "4343234", "bonus": "true"})
    bonus_labels, bonus_results, players = [], [], []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        header_row, num_cols = None, 0

        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            labels, results = [], []
            for cell in cells:
                label, result = parse_bonus_header(cell)
                if label:
                    labels.append(label)
                    results.append(result)
            if labels:
                bonus_labels, bonus_results = labels, results
                num_cols, header_row = len(labels), row
                break

        if not header_row:
            continue

        for row in rows:
            if row is header_row:
                continue
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) < 4:
                continue
            pos = cells[0].replace(".", "").strip()
            if not pos.isdigit():
                continue
            name = cells[2].strip()
            if not name:
                continue

            preds = []
            for i in range(num_cols):
                col = 3 + i
                raw = cells[col] if col < len(cells) else ""
                pred, _ = split_bonus_pred(raw)
                preds.append(pred)

            total = cells[-1].strip() or "0"
            players.append({"pos": int(pos), "name": name, "preds": preds, "total": total})

        if players:
            break

    return bonus_labels, bonus_results, players


def build_bonus_table(labels, results, players):
    if not labels or not players:
        return "⚠️ No bonus data found. Try again later."

    name_w = 7
    # Column width = max of label length and max pred length
    col_widths = []
    for i, lbl in enumerate(labels):
        vals = [p["preds"][i] for p in players if i < len(p["preds"])] + [results[i]]
        col_widths.append(max(len(lbl), max((len(v) for v in vals), default=1)))

    def make_row(name_col, pred_cols):
        parts = "|".join(
            pred_cols[i].center(col_widths[i]) for i in range(len(pred_cols))
        )
        return f"{name_col[:name_w].ljust(name_w)}|{parts}"

    # Total col
    def make_row_t(name_col, pred_cols, tot):
        parts = "|".join(
            pred_cols[i].center(col_widths[i]) for i in range(len(pred_cols))
        )
        return f"{name_col[:name_w].ljust(name_w)}|{parts}|{tot:>3}"

    header  = make_row_t("",    labels,  " T ")
    result_r= make_row_t("Res", results, "   ")
    divider = "-" * len(header)

    lines = ["🎯 *WorldPrediction2026 — Bonus*\n", "```"]
    lines.append(header)
    lines.append(result_r)
    lines.append(divider)
    for p in players:
        preds = [p["preds"][i] if i < len(p["preds"]) else "·" for i in range(len(labels))]
        lines.append(make_row_t(p["name"], preds, p["total"]))
    lines.append("```")
    lines.append("_· = no prediction yet · T = total pts_")
    return "\n".join(lines)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *WorldPrediction2026 Bot*\n\n"
        "Use /leaderboard to see the match prediction matrix.\n"
        "Use /bonus to see bonus question predictions.\n\n"
        "Type /help for all commands.",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Commands*\n\n"
        "/leaderboard — Match prediction matrix with scores and points\n\n"
        "/bonus — Bonus question predictions (World Champion, group winners, etc.)\n\n"
        "/start — Welcome message\n\n"
        "/help — This message",
        parse_mode="Markdown"
    )

async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching…")
    try:
        match_labels, match_results, players = fetch_matrix()
        text = build_match_table(match_labels, match_results, players)
    except Exception as e:
        logger.error(e)
        text = f"❌ Error: {e}"
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        await update.message.reply_text(chunk, parse_mode="Markdown")

async def cmd_bonus(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching bonus predictions…")
    try:
        labels, results, players = fetch_bonus()
        text = build_bonus_table(labels, results, players)
    except Exception as e:
        logger.error(e)
        text = f"❌ Error: {e}"
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        await update.message.reply_text(chunk, parse_mode="Markdown")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("⚠️  Set TELEGRAM_BOT_TOKEN env var or paste your token into BOT_TOKEN.")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("bonus",       cmd_bonus))
    logger.info("Bot is running…")
    app.run_polling()

if __name__ == "__main__":
    main()
