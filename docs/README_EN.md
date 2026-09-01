# Leziwu Super Value

**An evidence-driven financial research workspace for individual investors.**

[Live product](https://app.leziwu.com) · [Chinese README](../README.md) · [Documentation](INDEX.md) · [Issues](https://github.com/wu427692-alt/leizuw-super-analyst/issues)

![Leziwu market workspace](../apps/dsa-web/public/landing/screens/market-overview.jpg)

Leziwu connects market data, company filings, broker research, institutional notes, audio transcripts, company facts, public news, and investor discussions through a shared evidence store. The product is organized around one research loop:

1. **Today** — establish the market environment and identify material changes.
2. **Opportunities** — turn themes, events, and market consensus into candidates.
3. **Stock decision** — separate supporting, opposing, and still-missing evidence.
4. **Deep research** — create company or industry reports with traceable sources.
5. **Tasks and validation** — run transcription, data packaging, and quantitative research in the background.

## Data sources

- Tushare Pro: prices, fundamentals, valuation, capital flows, research, news, and trading events.
- CNInfo: company announcements and original PDF filings.
- ZSXQ MCP: institutional notes, images, files, and audio.
- Tianyancha: registration, ownership, risk, intellectual-property, and operating facts.
- Public sources: intraday quotes, financial news, and public investor commentary.
- SQLite: a shared local evidence and task index across all workspaces.

## Quick start

```bash
git clone https://github.com/wu427692-alt/leizuw-super-analyst.git
cd leizuw-super-analyst
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

cd apps/dsa-web
npm install
npm run build
cd ../..
python main.py --serve-only --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Production credentials, databases, logs, attachments, and exports are never stored in this repository.

This project supports information organization, research, and strategy validation. It is not investment advice and does not place trades for users.

## License

[MIT](../LICENSE). Original upstream copyright notices are retained; the Leziwu product extensions are maintained by `wu427692-alt`.
