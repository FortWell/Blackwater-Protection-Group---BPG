# Office of Community Investigations - OCI Bot

Official bot for Office of Community Investigations - OCI.

Features:
- Embed module: `/say`, `/send-message`, `/restore`
- Ticket module: `/ticket-panel` with Management, Security, and General flows
- Application module: `/apply` DM flow + AI-likelihood auto deny at threshold
- Staff module: `/promote`, `/infract`
- Local dashboard: multi-slot start/stop/restart panel for local bot processes

Setup:

1. Create and activate a virtual environment.
2. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```
3. Set your environment variables from `.env.example` or your host panel.
4. Run the bot:
   ```powershell
   python main.py
   ```

Local dashboard:

- Run `python dashboard.py` to open the control panel in your browser.
- The dashboard defaults to `http://127.0.0.1:8080`.
- Slot `primary` uses `.env`.
- Slot `secondary` uses `.env.secondary` copied from `.env.example`.
- From there you can start, stop, and restart each configured bot process.

Docker deploy:

- `Dockerfile` is included.
- Set the container start command to `python main.py`.
- Make sure the required environment variables are configured on the host.

Notes:

- The bot exposes a health endpoint automatically when `PORT` is provided.
- The dashboard uses `PORT` for the bot status server and `DASHBOARD_PORT` for itself.
- Separate bot instances need separate env files and database paths.
