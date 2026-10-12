# Moving Nexus (main app) to a new Mac (e.g. an M1 Max with LM Studio + Hermes)

Everything uses paths relative to this folder. `.venv`, `logs/` and `data/` belong to one machine
and are rebuilt or copied on purpose (see below).

## On the old Mac
```bash
cd /path/to/digital-marketing-agent
git status                      # commit or stash your work
./scripts/make_portable.sh --include-brands   # → dist/nexus-portable-<date>.zip (no .venv, logs or secrets)
```
Copy **separately and privately** if you want them:
- `.env` (API keys, relay token)
- `data/tasks/tasks.json` (your task calendar)
- `logs/profiles/` (browser logins; it's simpler to log in again)

You can also use `git clone` / `git bundle` instead of the zip.

## On the new Mac
```bash
unzip nexus-portable-*.zip -d ~/Documents/Agents/ && cd ~/Documents/Agents/digital-marketing-agent
./scripts/mac_setup_py39.sh --fresh          # new .venv (Python 3.9+; Homebrew 3.11+ works too)
cp /secure/place/.env .env                   # or edit the .env that was created from .env.example
./start.sh --check && ./start.sh             # relay + scheduler on http://127.0.0.1:8712/app/
```
Log in to the social accounts again:
`.venv/bin/python agent/social_browser_agent.py login --platform x` (and `instagram`, `facebook`).

## LM Studio + Hermes (M1 Max)
1. Install LM Studio and download a Hermes model. With an M1 Max you can run Hermes 3 8B or a
   larger Hermes 4 GGUF/MLX build if it fits in RAM.
2. Go to **Developer → Start Server** (port 1234) and load the model with 16k+ context.
3. In Nexus, choose **Settings → AI engine → Local AI → LM Studio**, or pick *LM Studio* in
   **Nexus Chat**.
4. Test it: `.venv/bin/python agent/nexus_chat.py "plan 3 Instagram posts for next week" --brain lmstudio`.
   This only creates tasks that wait for your approval. Nothing is posted.

For Hermes Agent / MCP, see [docs/tutorials/HERMES.md](docs/tutorials/HERMES.md).

## Start at login (optional)
Run `./scripts/install_daemon.sh`. It writes a LaunchAgent that points at **this** folder. Use a
different label per folder (`NEXUS_DAEMON_LABEL`) if a client build also runs on the Mac.

## Verify
```bash
./scripts/run_tests.sh --python      # all Python tests
curl -s http://127.0.0.1:8712/health
```
