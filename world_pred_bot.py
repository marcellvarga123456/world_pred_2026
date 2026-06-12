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
    )
}

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


def clean_pred(raw):
    """Kicktipp sometimes appends points to the prediction: '1-09' = pred '1-0' + 9pts."""
    raw = raw.strip()
    if not raw or raw == "---":
        return raw or "-"
    m = re.match(r'^(\d{1,2}-\d{1,2})(\d+)?$', raw)
    if m:
        pred = m.group(1)
        parts = pred.split("-")
        if len(parts) == 2 and len(parts[1]) > 1:
            return f"{parts[0]}-{parts[1][0]}"
        return pred
    return raw


def fetch_matrix():
    r = requests.get(URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text("\n")

    match_labels  = []
    match_results = []
    players = []
    header_found = False
    num_matches  = 0

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        if re.match(r'^\|[-| ]+\|$', line):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]

        if not header_found:
            cols_l, cols_r = [], []
            for cell in cells:
                m = re.match(r'^([A-Z]{2,4})\s+([A-Z]{2,4})\s+([\d]+-[\d]+|---)$', cell)
                if m:
                    cols_l.append(f"{m.group(1)} {m.group(2)}")
                    cols_r.append(m.group(3))
            if cols_l:
                match_labels  = cols_l
                match_results = cols_r
                num_matches   = len(cols_l)
                header_found  = True
            continue

        pos = cells[0].replace(".", "").strip()
        if not pos.isdigit():
            continue
        name = cells[2].strip() if len(cells) > 2 else ""
        if not name:
            continue

        preds = []
        for i in range(num_matches):
            col = 3 + i
            raw = cells[col] if col < len(cells) else ""
            preds.append(clean_pred(raw))

        pts   = cells[-4].strip() if len(cells) >= 4 else "0"
        total = cells[-1].strip() if cells             else "0"

        players.append({
            "pos":   int(pos),
            "name":  name,
            "preds": preds,
            "pts":   pts   or "0",
            "total": total or "0",
        })

    return match_labels, match_results, players


def build_table(match_labels, match_results, players):
    if not match_labels or not players:
        return "⚠️ No data found. Try again later."

    name_w = max(len(p["name"]) for p in players)
    name_w = max(name_w, 5)

    # Short 3-letter col headers like MEX, KOR, CAN ...
    short = [lbl.split()[0][:3] for lbl in match_labels]
    col_w = 5  # enough for "2-1" or "---"

    def row(name_col, pred_cols, pts_col, tot_col):
        return (
            name_col.ljust(name_w) + "  " +
            "  ".join(c.center(col_w) for c in pred_cols) +
            f"  {pts_col:>3}  {tot_col:>3}"
        )

    header   = row("Name",  short,         " P ", " T ")
    score_r  = row("Score", match_results, "   ", "   ")
    divider  = "-" * len(header)

    lines = ["🏆 *WorldPrediction2026*\n", "```"]
    lines.append(header)
    lines.append(score_r)
    lines.append(divider)

    for p in players:
        preds = [p["preds"][i] if i < len(p["preds"]) else "-" for i in range(len(match_labels))]
        lines.append(row(p["name"], preds, p["pts"], p["total"]))

    lines.append("```")
    return "\n".join(lines)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *WorldPrediction2026 Bot*\n\nUse /leaderboard to see the prediction matrix.",
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
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    logger.info("Bot is running…")
    app.run_polling()

if __name__ == "__main__":
    main()