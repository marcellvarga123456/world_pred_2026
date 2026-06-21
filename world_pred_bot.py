"""
Kicktipp WorldPrediction2026 — Telegram Bot
/leaderboard — shows the prediction matrix exactly as on the website
/bonus       — shows the bonus questions (one message per question)
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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def parse_header_cell(cell):
    m = re.match(r'^([A-Z]+)([\d]+-[\d]+|---)$', cell)
    if not m:
        return None, None
    teams  = m.group(1)
    result = m.group(2)
    t1, t2 = teams[:3], teams[3:]
    if not t2:
        return None, None
    return f"{t1} {t2}", result


def split_pred(raw):
    raw = raw.strip()
    if not raw or raw == "---":
        return raw or "-", ""
    m = re.match(r'^(\d+)-(\d+)$', raw)
    if m:
        home, away_pts = m.group(1), m.group(2)
        if len(away_pts) > 1:
            return f"{home}-{away_pts[0]}", away_pts[1:]
        return f"{home}-{away_pts}", ""
    return raw, ""


def split_bonus(raw):
    raw = raw.strip()
    if not raw or raw in ("---", "-"):
        return raw or "-", ""
    if raw.isdigit():
        return raw, ""
    m = re.match(r'^(.+?)(\d{1,2})$', raw)
    if m and not m.group(1).isdigit():
        return m.group(1), m.group(2)
    return raw, ""


# ---------------------------------------------------------------------------
# /leaderboard  (unchanged)
# ---------------------------------------------------------------------------

def fetch_matrix():
    r = requests.get(URL, headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    match_labels, match_results, players = [], [], []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
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
                raw = cells[3 + i] if 3 + i < len(cells) else ""
                pred, pts = split_pred(raw)
                preds.append(pred)
                pts_list.append(pts)
            players.append({
                "pos": int(pos), "name": name,
                "preds": preds, "pts": pts_list,
                "total": cells[-1].strip() or "0",
            })
        if players:
            break
    return match_labels, match_results, players


def build_table(match_labels, match_results, players):
    if not match_labels or not players:
        return "⚠️ No data found. Try again later."
    name_w, col_w = 7, 3
    home = [l.split()[0][:3] for l in match_labels]
    away = [l.split()[1][:3] if len(l.split()) > 1 else "   " for l in match_labels]

    def row(n, cols, t):
        return n[:name_w].ljust(name_w) + " " + " ".join(c.center(col_w) for c in cols) + f" {t:>2}"

    lines = ["🏆 *WorldPrediction2026*\n", "```",
             row("", home, " "), row("", away, "T"),
             row("Score", match_results, " "), "-" * len(row("", home, " "))]
    for p in players:
        preds = [p["preds"][i] if i < len(p["preds"]) else "-" for i in range(len(match_labels))]
        lines.append(row(p["name"], preds, p["total"]))
    lines += ["```", "_\\- = no prediction yet · T = total_"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /bonus
# ---------------------------------------------------------------------------

def fetch_bonus_matrix():
    r = requests.get(URL + "?bonus=true", headers=HEADERS, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    bonus_labels, correct_answers, players = [], [], []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_row, num_bonus = None, 0

        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            if len(cells) < 4:
                continue
            labels = cells[3:]
            if not any(l.strip() for l in labels):
                continue
            if any(re.match(r'^[A-Z]+(\d+-\d+|---)$', l) for l in labels):
                continue
            bonus_labels, num_bonus, header_row = labels, len(labels), row
            break

        if not header_row:
            continue

        remaining = [row for row in rows if row is not header_row]

        # optional "correct answer" row right after header
        if remaining:
            fc = [c.get_text(strip=True) for c in remaining[0].find_all(["th", "td"])]
            fp = fc[0].replace(".", "").strip() if fc else ""
            if not fp.isdigit() and len(fc) >= 4:
                correct_answers = fc[3:3 + num_bonus]
                remaining = remaining[1:]

        for row in remaining:
            cells = [c.get_text(strip=True) for c in row.find_all("td")]
            if len(cells) < 4:
                continue
            pos = cells[0].replace(".", "").strip()
            if not pos.isdigit():
                continue
            name = cells[2].strip()
            if not name:
                continue
            answers, pts_list = [], []
            for i in range(num_bonus):
                raw = cells[3 + i] if 3 + i < len(cells) else ""
                a, pt = split_bonus(raw)
                answers.append(a)
                pts_list.append(pt)
            # last cell: total if numeric, otherwise compute from pts_list
            last = cells[-1].strip() if cells else "0"
            if last.isdigit():
                total = last
            else:
                total = str(sum(int(pt) for pt in pts_list if pt.isdigit()))
            players.append({
                "pos": int(pos), "name": name,
                "answers": answers, "pts": pts_list,
                "total": total,
            })
        if players:
            break

    return bonus_labels, correct_answers, players


def build_bonus_messages(bonus_labels, correct_answers, players):
    """Return a list of message strings — one per question + a totals summary."""
    if not bonus_labels or not players:
        return ["⚠️ No bonus data found. Try again later."]

    messages = []

    for idx, label in enumerate(bonus_labels):
        correct = None
        if idx < len(correct_answers) and correct_answers[idx] not in ("-", "---", ""):
            correct = correct_answers[idx]

        lines = [f"📌 *{label}*"]
        if correct:
            lines.append(f"✅ Answer: {correct}")
        lines.append("")

        for p in players:
            answer = p["answers"][idx] if idx < len(p["answers"]) else "-"
            pts    = p["pts"][idx]    if idx < len(p["pts"])    else ""

            if correct and answer not in ("-", "---"):
                if pts and int(pts) > 0:
                    status = f"✅ {pts}pts"
                elif pts == "0":
                    status = "❌ 0pts"
                else:
                    status = "·"
            else:
                status = "·"

            lines.append(f"{p['pos']}. {p['name']} — {answer} {status}")

        messages.append("\n".join(lines))

    # totals summary
    sum_lines = ["📊 *Bonus Totals*", ""]
    for p in players:
        sum_lines.append(f"{p['pos']}. {p['name']} — {p['total']}pts")
    messages.append("\n".join(sum_lines))

    return messages


# ---------------------------------------------------------------------------
# Bot commands
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *WorldPrediction2026 Bot*\n\n"
        "Use /leaderboard to see the prediction matrix.\n"
        "Use /bonus to see bonus question answers.\n\n"
        "Type /help for all commands.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Commands*\n\n"
        "/leaderboard — Full prediction matrix showing everyone's tips "
        "for each match, the actual score, and current points\n\n"
        "/bonus — Bonus questions: one message per question showing "
        "all players' answers and points earned\n\n"
        "/start — Welcome message\n\n"
        "/help — This message",
        parse_mode="Markdown",
    )


async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching…")
    try:
        ml, mr, pl = fetch_matrix()
        text = build_table(ml, mr, pl)
    except Exception as e:
        logger.error(e)
        text = f"❌ Error: {e}"
    for chunk in [text[i:i + 4096] for i in range(0, len(text), 4096)]:
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_bonus(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⏳ Fetching bonus…")
    try:
        bonus_labels, correct_answers, players = fetch_bonus_matrix()
        messages = build_bonus_messages(bonus_labels, correct_answers, players)
    except Exception as e:
        logger.error(e)
        messages = [f"❌ Error: {e}"]
    for msg in messages:
        for chunk in [msg[i:i + 4096] for i in range(0, len(msg), 4096)]:
            await update.message.reply_text(chunk, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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
