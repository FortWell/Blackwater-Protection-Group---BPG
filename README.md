# NYCRPP Federal Reserve Bot

Starter `discord.py` bot with:
- Embed module: `/say`, `/send-message`, `/restore`
- Ticket module: `/ticket-panel` with Management/Security/General create buttons + close flow
- Application module: `/apply` DM flow + AI-likelihood auto deny at threshold
- Staff module: `/promote`, `/infract`

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill all IDs/token.
4. Run:
   ```powershell
   python main.py
   ```

## Koyeb Deploy (Easiest)

This repo is ready for Koyeb Docker deploy (`Dockerfile` included).

1. Push this project to GitHub.
2. In Koyeb: create a new app from GitHub repo.
3. Service type: Web Service.
4. Build method: Dockerfile.
5. Start command: use Docker default (`python main.py`).
6. Add environment variables from your local `.env` (do not upload `.env` file itself).
7. Deploy.

Notes:
- The bot opens a health endpoint automatically when `PORT` is provided by Koyeb.
- Use one bot instance only (already enforced in code).

## Important note about AI detection

Current implementation uses an internal heuristic function (`estimate_ai_likelihood`) to unblock development quickly.
Before production, replace this with a real detector API/provider.
