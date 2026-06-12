"""
Kicktipp WorldPrediction2026 — Telegram Bot
/leaderboard — shows the prediction matrix exactly as on the website
"""

import os
import re
import logging
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
URL = "https://www.kicktipp.com/worldprediction2026/leaderboard"
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


def parse_header_cell(cell):
    """
    Parse a header cell like 'MEXRSA2-0' or 'QATCH---' into (label, result).
    Format: TEAM1(3chars) + TEAM2(2-4chars) + score_or_dashes
    """
    m = re.match(r'^([A-Z]+)([\d]+-[\d]+|---)$', cell)
    if not m:
        return None, None
    teams  = m.group(1)
    result = m.group(2)
    t1 = teams[:3]
    t2 = teams[3:]
    if not t2:
        return None, None
    return f"{t1} {t2}", result


def split_pred(raw):
    """
    Kicktipp concatenates prediction + points earned into one string:
      '2-09' -> pred='2-0', pts='9'
      '1-03' -> pred='1-0', pts='3'
      '2-19' -> pred='2-1', pts='9'
      '1-1'  -> pred='1-1', pts=''   (match not finished yet)
      '---'  -> pred='---', pts=''
      ''     -> pred='-',   pts=''
    Rule: away score is always exactly 1 digit; extra digits are points.
    """
    raw = raw.strip()
    if not raw or raw == "---":
        return raw or "-", ""
    m = re.match(r'^(\d+)-(\d+)$', raw)
    if m:
        home     = m.group(1)
        away_pts = m.group(2)
        if len(away_pts) > 1:
            return f"{home}-{away_pts[0]}", away_pts[1:]
        return f"{home}-{away_pts}", ""
    return raw, ""


def pred_emoji(result, pred):
    if not result or result == "---" or not pred or pred in ("-", "---"):
        return ""
    try:
        rh, ra = map(int, result.split("-"))
        ph, pa = map(int, pred.split("-"))
        if rh == ph and ra == pa:
            return "✅"
        if (rh > ra and ph > pa) or (rh < ra and ph < pa) or (rh == ra and ph == pa):
            return "🎯"
        return "❌"
    except Exception:
        return ""


def fetch_matrix():
    r = requests.get(URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    match_labels  = []
    match_results = []
    players = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue

        # Find the header row: contains cells matching TEAM1TEAM2score pattern
        header_row  = None
        num_matches = 0

        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            labels, results = [], []
            for cell in cells:
                label, result = parse_header_cell(cell)
                if label:
                    labels.append(label)
                    results.append(result)
            if labels:
                match_labels  = labels
                match_results = results
                num_matches   = len(labels)
                header_row    = row
                break

        if not header_row:
            continue

        # Parse player rows
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
            pts_list = []
            for i in range(num_matches):
                col = 3 + i
                raw = cells[col] if col < len(cells) else ""
                pred, pts = split_pred(raw)
                preds.append(pred)
                pts_list.append(pts)

            md_pts = cells[-4].strip() if len(cells) >= 4 else "0"
            total  = cells[-1].strip() if cells             else "0"

            players.append({
                "pos":    int(pos),
                "name":   name,
                "preds":  preds,
                "pts":    pts_list,
                "md_pts": md_pts or "0",
                "total":  total  or "0",
            })

        if players:
            break

    return match_labels, match_results, players


def build_table(match_labels, match_results, players):
    if not match_labels or not players:
        return "⚠️ No data found. Try again later."

    name_w = 7   # truncate names to 7 chars
    col_w  = 3   # "2-0" and "---" are both 3 chars

    home_teams = [lbl.split()[0][:3] for lbl in match_labels]
    away_teams = [lbl.split()[1][:3] if len(lbl.split()) > 1 else "   " for lbl in match_labels]

    def make_row(name_col, pred_cols, pts_col, tot_col):
        return (
            name_col[:name_w].ljust(name_w) + " " +
            " ".join(c.center(col_w) for c in pred_cols) +
            f" {pts_col:>2} {tot_col:>2}"
        )

    header1 = make_row("",      home_teams,    " ", " ")
    header2 = make_row("",      away_teams,    "P", "T")
    score_r = make_row("Score", match_results, " ", " ")
    divider = "-" * len(header1)

    lines = ["🏆 *WorldPrediction2026*\n", "```"]
    lines.append(header1)
    lines.append(header2)
    lines.append(score_r)
    lines.append(divider)

    for p in players:
        preds = [p["preds"][i] if i < len(p["preds"]) else "-" for i in range(len(match_labels))]
        lines.append(make_row(p["name"], preds, p["md_pts"], p["total"]))

    lines.append("```")
    lines.append("_\\- = no prediction yet · P = matchday pts · T = total_")
    return "\n".join(lines)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *WorldPrediction2026 Bot*\n\nUse /leaderboard to see the prediction matrix.\n\nType /help for all commands.",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Commands*\n\n"
        "/leaderboard — Full prediction matrix showing everyone's tips for each match, the actual score, and current points\n\n"
        "/start — Welcome message\n\n"
        "/help — This message",
        parse_mode="Markdown"
    )

async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching…")
    try:
        match_labels, match_results, players = fetch_matrix()
        text = build_table(match_labels, match_results, players)
    except Exception as e:
        logger.error(e)
        text = f"❌ Error: {e}"
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        await update.message.reply_text(chunk, parse_mode="Markdown")


def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("⚠️  Set TELEGRAM_BOT_TOKEN env var or paste your token into BOT_TOKEN.")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("help",        cmd_help))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    logger.info("Bot is running…")
    app.run_polling()

if __name__ == "__main__":
    main()
