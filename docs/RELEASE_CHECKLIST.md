# Release Checklist

Before pushing:

- Confirm `.env` is not tracked:
  - `git ls-files .env` should return nothing.
- Review staged files:
  - `git status`
- Run local compile sanity check:
  - `python -m compileall -q bot cogs main.py`
- Verify required env keys exist in `.env` for your deployment target.
- Rotate secrets immediately if you suspect exposure.

Push flow:

- `git add .`
- `git commit -m "your message"`
- `git push`

After pushing:

- Confirm GitHub Actions CI passed.
- If deploying, confirm host environment variables are set (do not upload `.env`).

