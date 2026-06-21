"""
Kicktipp WorldPrediction2026 — Telegram Bot
/leaderboard — shows the prediction matrix exactly as on the website
/bonus       — takes the data and sends a perfectly formatted, zoomable IMAGE
"""

import os
import re
import textwrap
import logging
import requests
import pandas as pd
import matplotlib
matplotlib.use('Agg') # Required for servers without a screen/GUI
import matplotlib.pyplot as plt
from io import BytesIO
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
    teams, result = m.group(1), m.group(2)
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
    all_tables = soup.find_all("table")

    question_texts = []
    correct_answers_upper = []

    # Step 1: Upper table (real question texts)
    for table in all_tables:
        for row in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            if len(cells) < 3:
                continue
            first = cells[0].replace(".", "").strip()
            if not first.isdigit():
                continue
            q = cells[1].strip()
            if len(q) > 10 and not re.match(r'^[A-Z]{2,}[\d\-]+$', q):
                question_texts.append(q)
                ca = cells[2].strip()
                correct_answers_upper.append(ca if ca not in ("-", "---", "") else "")

    # Step 2: Lower matrix table
    matrix_labels = []
    correct_from_matrix = []
    players = []
    TOTAL_KEYWORDS = {"pkt", "punkte", "total", "sum", "t", "pts", "p"}

    for table in all_tables:
        rows = table.find_all("tr")
        if not rows:
            continue
        header_row, num_cols = None, 0

        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
            if len(cells) < 5:
                continue
            labels = cells[3:]
            if not any(l.strip() for l in labels):
                continue
            if any(re.match(r'^[A-Z]+(\d+-\d+|---)$', l) for l in labels):
                continue

            clean = list(labels)
            while clean:
                last = clean[-1].strip().lower().rstrip(".")
                if not clean[-1].strip() or last in TOTAL_KEYWORDS:
                    clean.pop()
                else:
                    break
            if not clean:
                continue

            matrix_labels = clean
            num_cols = len(clean)
            header_row = row
            break

        if not header_row:
            continue

        remaining = [r for r in rows if r is not header_row]

        if remaining:
            fc = [c.get_text(strip=True) for c in remaining[0].find_all(["th", "td"])]
            fp = fc[0].replace(".", "").strip() if fc else ""
            if not fp.isdigit() and len(fc) >= 4:
                correct_from_matrix = fc[3:3 + num_cols]
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
            raw = [cells[3 + i].strip() if 3 + i < len(cells) else "" for i in range(num_cols)]
            total_raw = cells[3 + num_cols].strip() if 3 + num_cols < len(cells) else ""
            players.append({"pos": int(pos), "name": name, "raw": raw, "total_raw": total_raw})

        if players:
            break

    # Step 3: Map questions to matrix cols & handle multi-col last question
    num_q = len(question_texts)
    num_c = len(matrix_labels)

    if num_q == 0:
        question_texts = list(matrix_labels)
        num_q = num_c

    if num_c < num_q:
        question_texts = question_texts[:num_c]
        correct_answers_upper = correct_answers_upper[:num_c]
        num_q = num_c

    last_q_span = max(1, num_c - num_q + 1)

    # Correct answers
    correct_answers = []
    ci = 0
    for qi in range(num_q):
        span = last_q_span if (qi == num_q - 1 and last_q_span > 1) else 1
        if qi < len(correct_answers_upper) and correct_answers_upper[qi]:
            correct_answers.append(correct_answers_upper[qi])
        else:
            parts = []
            for j in range(span):
                if ci + j < len(correct_from_matrix):
                    v = correct_from_matrix[ci + j]
                    if v and v not in ("-", "---"):
                        parts.append(v)
            correct_answers.append(",".join(parts))
        ci += span

    # Player answers
    for p in players:
        answers, pts_list = [], []
        ci = 0
        for qi in range(num_q):
            span = last_q_span if (qi == num_q - 1 and last_q_span > 1) else 1
            if span > 1:
                parts, pts = [], ""
                for j in range(span):
                    if ci + j < len(p["raw"]):
                        a, pt = split_bonus(p["raw"][ci + j])
                        if a not in ("-", "---"):
                            parts.append(a)
                        if pt and pt.isdigit():
                            pts = pt
                answers.append(",".join(parts) if parts else "-")
                pts_list.append(pts)
            else:
                if ci < len(p["raw"]):
                    a, pt = split_bonus(p["raw"][ci])
                    answers.append(a)
                    pts_list.append(pt)
                else:
                    answers.append("-")
                    pts_list.append("")
            ci += span

        tr = p["total_raw"]
        if tr and tr.replace(",", ".").lstrip("-").isdigit():
            p["total"] = tr
        else:
            p["total"] = str(sum(int(pt) for pt in pts_list if pt.isdigit()))
            
        p["answers"] = answers
        p["pts"] = pts_list
        del p["raw"], p["total_raw"]

    return question_texts, correct_answers, players


def _wrap(text, width=20):
    """Wrap long strings with newlines so they stack inside table cells."""
    return "\n".join(textwrap.wrap(str(text), width=width) or [''])


def build_bonus_image(question_texts, correct_answers, players):
    """Converts parsed bonus data into a high-quality PNG image."""
    if not question_texts or not players:
        return None

    # 1. Build Data for Pandas
    data = {}
    for i, q in enumerate(question_texts):
        col_data = []
        # First row is the correct answer
        ans = correct_answers[i] if i < len(correct_answers) and correct_answers[i] else "-"
        col_data.append(f"✅ {_wrap(ans, 18)}")
        
        # Player answers
        for p in players:
            a = p["answers"][i] if i < len(p["answers"]) else "-"
            col_data.append(_wrap(a, 18))
        data[q] = col_data

    index = ["Answer"] + [f"{p['pos']}. {p['name']}" for p in players]
    df = pd.DataFrame(data, index=index)

    # Add Total column
    df["Total"] = [""] + [f"{p['total']} pts" for p in players]

    # 2. Plot with Matplotlib
    num_rows, num_cols = df.shape
    
    # Dynamically size the figure based on columns and rows to prevent squishing
    fig_width = max(8, num_cols * 3.5)
    fig_height = max(4, num_rows * 0.6)
    
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    ax.axis('off')
    ax.axis('tight')

    # Create table
    table = ax.table(cellText=df.values, colLabels=df.columns, loc='center', cellLoc='center')
    
    # 3. Styling
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.8) # Increase row height for readability

    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor('#CCCCCC')
        if row == 0:  # Header row (Questions)
            cell.set_facecolor('#2E7D32') # Dark green
            cell.set_text_props(weight='bold', color='white')
            cell.set_height(0.08)
        elif row == 1:  # Answer row
            cell.set_facecolor('#E8F5E9') # Light green
            cell.set_text_props(color='#2E7D32')
        else:  # Player rows
            cell.set_facecolor('#F9F9F9' if row % 2 == 0 else '#FFFFFF')

    plt.tight_layout()
    
    # 4. Save to memory buffer
    buf = BytesIO()
    plt.savefig(buf, format="PNG", bbox_inches='tight', dpi=150, facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Bot commands
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *WorldPrediction2026 Bot*\n\n"
        "Use /leaderboard to see the prediction matrix.\n"
        "Use /bonus to see bonus questions as an image.\n\n"
        "Type /help for all commands.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Commands*\n\n"
        "/leaderboard — Full prediction matrix (text)\n\n"
        "/bonus — Bonus questions matrix sent as a zoomable image\n\n"
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
    await update.message.reply_text("⏳ Fetching bonus & generating image…")
    try:
        bonus_labels, correct_answers, players = fetch_bonus_matrix()
        image_buffer = build_bonus_image(bonus_labels, correct_answers, players)
        
        if image_buffer:
            await update.message.reply_photo(
                photo=image_buffer, 
                caption="🏆 *WorldPrediction2026 — Bonus Questions*", 
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("⚠️ No bonus data found. Try again later.")
            
    except Exception as e:
        logger.error(e)
        await update.message.reply_text(f"❌ Error: {e}")


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
