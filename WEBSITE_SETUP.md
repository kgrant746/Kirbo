# Kirbo website setup

The bot and website run together on the computer that normally runs Kirbo. Push this repository, pull it on that computer, then follow these steps there. The domain is prepared in the code, but DNS and Discord settings require your accounts.

## 1. Configure Discord sign-in

Open [Discord Developer Portal](https://discord.com/developers/applications), select **Kirbo**, and open **OAuth2**.

- Copy the Application/Client ID and OAuth2 Client Secret into the bot computer’s `.env` as `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET`. The client secret is different from the bot token.
- Under Redirects, add exactly `https://bigboicucks.org/auth/callback` and save.
- Keep `GUILD_ID` set to Team Rocket 2.0. No extra bot invite or new bot account is needed.

Add these settings to the existing `.env` (keep its bot and Spotify settings):

```dotenv
WEB_ENABLED=true
WEB_PUBLIC_URL=https://bigboicucks.org
WEB_BIND=127.0.0.1
WEB_PORT=8080
DISCORD_CLIENT_ID=your_application_id
DISCORD_CLIENT_SECRET=your_oauth_client_secret
```

Requested OAuth permissions are identity and membership information for the configured server. Kirbo does not request email, messages, or permission to join servers on users’ behalf. It verifies server membership on login and reuses successful verification for up to 30 seconds across browser sessions. After that, membership must be verified again before access continues. Existing blacklisted users are denied too.

## 2. Point your domain at the bot computer

Recommended: a **Cloudflare Tunnel**, which provides HTTPS without opening a router port. It must run on the bot computer. If another website currently uses `bigboicucks.org`, publishing Kirbo at that hostname replaces its route; retain any unrelated DNS records.

1. Add `bigboicucks.org` to your Cloudflare account. If its DNS is hosted elsewhere, follow Cloudflare’s instructions to change the domain’s nameservers at your registrar. Copy all existing DNS records, especially email records.
2. In Cloudflare’s dashboard, open **Networking → Tunnels** (sometimes under Zero Trust / Networks / Connectors), create a tunnel called `kirbo`, and select Windows.
3. Install `cloudflared` on the bot computer and run the **service installation command shown by Cloudflare** in an administrator terminal. Its tunnel token is secret; do not commit it or paste it into the website source. As a Windows service, it starts automatically when the computer starts.
4. Add a **Published application route / Public hostname** for `bigboicucks.org`, with an empty subdomain and service **HTTP → localhost:8080**. Do not add a Cloudflare Access login gate: Kirbo already requires Discord login.
5. Run `start_bot.bat`, then open **https://bigboicucks.org**. Sign in using a Team Rocket 2.0 account. Check a non-member account is denied too.

Cloudflare’s [tunnel setup guide](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/) explains the current dashboard and connector installation. Allow event streams through any additional proxy; disable buffering/caching for `/api/*` and `/auth/*`. Keep Cloudflare caching set to the normal default, not “cache everything.”

## 3. Normal startup

Double-click **start_bot.bat**. It uses the project’s virtual environment, installs missing requirements, and starts both services. It no longer uses a hard-coded checkout path. `setup.bat` still creates the environment and updates dependencies when you explicitly run it. Keep your working FFmpeg and Deno installations.

Cloudflared runs as the Windows service installed above; you do not start another bot or website terminal. The website works while the bot computer and tunnel are running. To temporarily run only the Discord bot, set `WEB_ENABLED=false`.

## How it behaves

- Discord slash commands and browsers share one player and queue. Changes stream to all signed-in browsers; no refresh or second bot instance is involved.
- Users must join Kirbo’s Discord voice channel for playback controls. All authorised server members can edit shared queues and playlists. The website implements the music, playlist and activity features requested; moderation and casino commands remain available through Discord.
- A random HttpOnly session cookie remembers a browser for 90 days, renewed when it visits again. OAuth tokens remain on the bot computer and are refreshed automatically. Discord revocation, removed membership, cleared browser cookies, or deleted session storage can require sign-in again; permanent login cannot be guaranteed.
- Every protected request requires a valid session and successful membership verification from within the last 30 seconds. Open event streams recheck periodically. A removed member can retain access for the remainder of that cache window; after expiry, failed verification blocks commands. This bounded cache allows rapid queue additions without a Discord API request per click.
- Saved `.txt` playlists are imported once into `data/controller.sqlite3`; originals are preserved. After migration, edit playlists through the website. `/playlist christmas.txt` still finds the imported `christmas` playlist. Back up the SQLite database with the bot stopped, or use SQLite’s backup tool. It contains playlists, recent activity and private login tokens. Keep `data/` private and out of Git.
- Queue rows waiting to play can be reordered or removed. The current track is pinned above them; use Skip to remove the active track. Right-click playlist songs to queue, play next, edit or remove them. Alt+Up/Down also reorders a focused row.
- Spotify imports save song references and metadata; Kirbo still finds and plays corresponding YouTube audio. Only playlists accessible to your configured Spotify application can be imported. Spotify application restrictions may reject some playlists.
- Queue and current playback live in memory and reset on restart. Playlist and log changes persist. Concurrent reorder or playlist edits are checked for conflicts so a stale browser cannot silently overwrite someone else’s edit.

## Local testing and troubleshooting

For localhost-only sign-in testing, register `http://127.0.0.1:8080/auth/callback` in Discord and set `WEB_PUBLIC_URL=http://127.0.0.1:8080`. Restore the HTTPS domain value before using the tunnel. Never expose the HTTP port directly to the internet.

“Sign-in not configured”: check both OAuth variables on the **bot computer**. “Invalid redirect”: the portal redirect and `WEB_PUBLIC_URL` must match exactly. A tunnel 502 generally means the bot/web server is not listening on port 8080. If Spotify returns an error, check its credentials and application playlist permissions. Never post tokens, the `.env`, or the database in troubleshooting logs.

Run automated checks with `venv\Scripts\python.exe -m unittest discover -s tests -v`.

### Playlist artwork

Missing song thumbnails in older playlists are looked up in the background when the website starts, saved in the database, and shared live with connected browsers. Unavailable videos keep the music-note placeholder; failed lookups are retried on a later restart.

Playlist covers default to the first track’s thumbnail. Open a playlist and click its cover (hover shows **Choose photo**) to upload a JPG, PNG or WebP up to 5 MB and 25 megapixels. Kirbo validates and resizes the image, removes image metadata, and shares it with all signed-in server members. Covers persist in `data/playlist-covers/` on the **bot computer**; back up that folder along with `data/controller.sqlite3`. Uploaded covers require the same sign-in and membership checks as the rest of the website. Running `start_bot.bat` installs the new Pillow image dependency automatically.
