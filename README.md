# Blackwater Protection Group Bot

Official bot for Blackwater Protection Group (BPG).

Features:
- Embed module: `/say`, `/send-message`, `/restore`
- Ticket module: `/ticket-panel` with Management, Security, and General flows
- Application module: `/apply` DM flow + AI-likelihood auto deny at threshold
- Staff module: `/promote`, `/infract`

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

Docker deploy:

- `Dockerfile` is included.
- Set the container start command to `python main.py`.
- Make sure the required environment variables are configured on the host.

Notes:

- The bot exposes a health endpoint automatically when `PORT` is provided.
- Run only one bot instance at a time.
