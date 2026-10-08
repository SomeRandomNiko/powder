# Powder

**Keep Steep playable after Ubisoft's servers are gone.** Powder is a self-hosted stand-in for the online
services Steep needs, plus a one-click launcher for Windows. It preserves _your own_ copy of the game:
progression, store, challenges and rewards all keep working without Ubisoft.

Powder is an unofficial fan preservation project. It is not affiliated with or endorsed by Ubisoft;
Steep and Ubisoft Connect are trademarks of Ubisoft. You need your own copy of Steep, from Steam or from
Ubisoft Connect. The repository contains no game files and no data from Ubisoft's servers.

## What it does

- **Server** (`powder-server`): one process that answers the game and Ubisoft Connect in place of Ubisoft:
  the UbiServices HTTPS API (all `*.ubi.com` hosts), the Quazal NEX/RendezVous server and Ubisoft
  Connect's demux protocol. Player state lives in SQLite.
- **Launcher** (`powder`): runs on the PC with the game. It checks and sets up what is needed there (server,
  DNS, certificate, Ubisoft Connect), starts Steep online against the server, and can undo all of it again.

## Game features

What Steep needs a server for, and how far Powder covers it.

| Status         | Meaning                                                                                                                 |
| -------------- | ----------------------------------------------------------------------------------------------------------------------- |
| ✅ Working     | Works like with Ubisoft's servers                                                                                       |
| 🟡 Partly      | The game accepts the answer, but the feature is limited, not verified everywhere, or not usable without technical steps |
| ❌ Not working | The game asks, the server answers with an empty or placeholder reply: the feature is missing                            |
| ⛔ Not planned | Not part of Steep's gameplay, or not worth emulating                                                                    |

### Getting online

| Feature                                         | Status | Notes                                                                                       |
| ----------------------------------------------- | ------ | ------------------------------------------------------------------------------------------- |
| Ubisoft Connect sign-in                         | ✅     | Any email/password; Connect stays signed in to the server                                   |
| Game login (Steam and Ubisoft Connect versions) | ✅     | Both versions of the final update (TU7)                                                     |
| Owned games / DLC ownership                     | ✅     | Steep and its add-ons, built in                                                             |
| Game configuration, season info                 | ✅     | Built in (the game-data pack's copy is used when installed)                                 |
| Several players on one server                   | 🟡     | Possible by mapping IP addresses to players (`[players.bind]`); no real player accounts yet |

### Progression and economy

| Feature                                          | Status | Notes                                                                                                                                  |
| ------------------------------------------------ | ------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| Level and XP per sport, playtime                 | ✅     | From the game's stat uploads; never goes down                                                                                          |
| Medals, dropzones, memorables, unlocks           | ✅     |                                                                                                                                        |
| Store: buying items with credits                 | ✅     | Needs the game-data pack (the catalogue); without it the store is empty                                                                |
| Challenge rewards (credits, items)               | ✅     | Needs the game-data pack (rewards are store offers)                                                                                    |
| Rider card and favorites                         | ✅     |                                                                                                                                        |
| Personal best per challenge                      | ✅     | Stored on the server                                                                                                                   |
| Achievements                                     | ✅     | Stored per player; names and icons in Ubisoft Connect need the game-data pack                                                          |
| Taking over your progress from Ubisoft's servers | 🟡     | Only from recordings of your own online sessions, via the command line (`admin import`); players can't make such recordings themselves |

### Competing and sharing

| Feature                           | Status | Notes                                                                       |
| --------------------------------- | ------ | --------------------------------------------------------------------------- |
| Leaderboards (challenge rankings) | ❌     | Always empty; the scores are already stored, the rankings are not built yet |
| Replays / ghosts of other players | ❌     | Uploads and downloads of replays are not stored or served                   |
| Player-made trails / challenges   | ❌     | Always an empty list                                                        |
| Seasons and tournaments           | 🟡     | No season running, as on Ubisoft's servers at the end; nothing changes      |

### Playing with others

| Feature                                                  | Status | Notes                                                                     |
| -------------------------------------------------------- | ------ | ------------------------------------------------------------------------- |
| Multiplayer sessions (other riders in the world, groups) | ❌     | The game opens its own session; players are never matched with each other |
| Matchmaking                                              | 🟡     | Answered, so the game does not stall; it never finds a match              |
| Friends list, invites, blocks                            | ❌     | Always empty                                                              |
| Other players' profiles (rider card, level)              | ❌     | Only your own                                                             |

### Ubisoft services around the game

| Feature                                                           | Status | Notes                                                                                   |
| ----------------------------------------------------------------- | ------ | --------------------------------------------------------------------------------------- |
| Ubisoft Club (actions, units, rewards)                            | ⛔     | Ubisoft's cross-game reward system: the action list is shown, no progress or rewards    |
| Cloud saves                                                       | ❌     | Switched off, so Connect never asks to sync; game settings stay on each PC              |
| News, recommendations, Ubisoft Connect store and free-games pages | ⛔     | Ubisoft's own content, not part of Steep                                                |
| Telemetry, crash reports                                          | ⛔     | Acknowledged and dropped (the game's error reports are kept in the server's log folder) |

## Install

You need Windows 10/11 with Ubisoft Connect, Steep's final update (TU7) from Steam or Ubisoft Connect.
A game-data pack (`.zip`, see below) is optional: it adds the store and challenge rewards.

1. Download `Powder-Setup-vX.Y.Z.exe` from the [Releases](../../releases) page and run it. It installs for
   your Windows user only (no admin rights). While Powder is not code-signed, Windows SmartScreen warns
   about an unknown app: **More info → Run anyway**.
2. Start **Powder** and press **Play**. The first time, it asks for a game-data pack (**Play without it**
   is fine), then walks you through what it needs to set up (every Windows prompt is explained before it
   appears).

Powder keeps your progress, the game-data pack and its settings in `%LOCALAPPDATA%\Powder`, apart from
the program, so updating (install the new version over the old one) never touches them. Uninstalling
(Windows settings → Apps) first opens Restore system to undo what Powder set up on this PC, then asks
whether to keep your data.

Commands for the installed program: `powder-cli.exe` in the install folder
(`%LOCALAPPDATA%\Programs\Powder`), e.g. `powder-cli server admin players`.

## Run from source

Needs [uv](https://docs.astral.sh/uv/) (it brings Python 3.12) and [zig](https://ziglang.org/) to build
the TLS helper. Files go to the current folder instead of `%LOCALAPPDATA%\Powder`.

```
native\build.ps1                                    # once: builds the TLS helper
uv run powder-server gamedata build <recordings> gamedata   # optional: the game-data pack (see below)
uv run powder                                       # the launcher: press Play
```

Other commands:

```
uv run powder-server run        # the server alone (uses ./config.toml, see config.example.toml)
uv run powder --status          # what the launcher has set up on this PC
uv run powder --restore         # undo it
uv run pytest
```

## Game-data pack

Powder runs without one: the server builds what the game and Ubisoft Connect need to go online
(`powder/server/builtin/`: Steep's owned-games entries, the SDK configuration, the game's start replies;
only ids, hostnames, URL templates and values written for Powder). What it cannot build is Ubisoft's
game data: the store catalogue (2,000+ offers, which also define challenge rewards), Ubisoft Club actions,
the achievement names and icons Connect shows, and which items each DLC grants. That data is not in this
repository. It comes as an optional pack in `gamedata/`, built from recordings of your own online
sessions; when installed, its replies take precedence over the built-in ones:

```
uv run powder-server gamedata build <recordings> gamedata    # allow-list + privacy check
uv run powder-server gamedata verify                         # manifest + file hashes
```

The builder copies only allow-listed, game-wide replies. Before writing the pack it collects every
identifying value from the recordings (account and profile ids, names, email, tickets, friends, other
players) and refuses to write the pack if any of them appears in it. Everything personal (profile,
friends, login, owned games beyond Steep) is generated by the server instead of replayed.

Ubisoft Connect's own interface comes from Ubisoft's CDN and keeps its settings in a browser cache, which
the server never overwrites (it answers "not modified"). If Connect has never been online on a PC, or its
cache was cleared, it fails with dolphin-032 until it has been started once normally (without Powder:
**Restore system**, sign in to your real account, then Play again).

The pack travels as one `.zip`: the launcher asks for it when it is missing (checking format and file
hashes before using it), and the same works from a console:

```
uv run powder-server gamedata zip steep-gamedata.zip            # the configured pack -> one .zip
uv run powder-server gamedata install steep-gamedata.zip        # .zip or folder -> the configured pack
```

## Players

A player who logs in for the first time starts like a new Ubisoft account: level 1, empty inventory,
`starting_credits`. To continue with your own progress, import it once from your recordings:

```
uv run powder-server admin import <recordings>      # level, credits, items, stats, rider card, achievements
uv run powder-server admin import <recordings> --replace
```

After the import the server never reads the recordings again. Recordings contain personal data (your
account, friends, other players on leaderboards), so `recordings/` is gitignored and nothing in the
repository depends on it.

## Launcher

`powder` (settings in `launcher.toml`, see `launcher.example.toml`; all optional) works with both
versions of Steep. It finds them wherever they are installed, from what Steam (its libraries and app
manifests) and Ubisoft Connect (its install records) note about them. With both installed it shows
**Play (Steam)** and **Play (Ubisoft Connect)**, otherwise a single **Play**. Pressing Play walks through
these steps; each step only checks unless something is missing:

1. **Game files**: finds the chosen Steep, Ubisoft Connect (and Steam for the Steam version), and checks
   that `Steep.exe` is a build the TLS helper supports.
2. **Game data**: the local server's game-data pack; if it is missing, it offers to install the `.zip`
   or to play without it.
3. **Server**: reaches it, or starts it locally (`server_mode = "local"`, the default) after checking that
   its ports are free (if not, it names the program using them). With `server_mode = "remote"` and
   `server_address` it uses a server on another PC instead, and warns if that server's major version
   differs.
4. **DNS**: every Ubisoft hostname (`powder/server/hostnames.py`) must resolve to the server address. If not,
   it shows the lines to add to your own DNS server, or adds them to the hosts file for you (the previous
   hosts file is backed up to `data/launcher/hosts-backups/`).
5. **Certificate**: gets the server's CA and, if Windows doesn't trust it yet, installs it for your user.
6. **Ubisoft Connect**: restarts it if it is not signed in to the server, then waits for its login (any
   email and password work).
7. **Steep**: starts it through Steam or Ubisoft Connect, attaches the TLS helper to the game and waits
   until it is online. Both versions start a short-lived starter process first; the helper attaches only
   to the real game from the chosen install. While the launcher stays open, the server keeps running, so
   Ubisoft Connect stays signed in and the next Play goes straight to the game.

Every Windows prompt is announced first: the launcher says why it is needed (the hosts file is a protected
system file, so it needs admin rights; Windows asks to confirm a new certificate) and only continues when
you press the button for it.

The TLS helper (`native/`) makes the game accept the server's certificate: `hook_guard.dll` traps the
game's certificate check without changing code bytes at first, `armer.exe` keeps that trap armed and then
patches the check once the game's integrity check is over. It only fits the `Steep.exe` builds listed in
`powder/launcher/game.py` (the Steam and the Ubisoft Connect download of the final update: the same
compiled game, packaged differently). Game files are never modified on disk.

### Restoring your PC

**Restore system…** undoes everything the launcher set up: it stops the server it started, quits
Ubisoft Connect (which otherwise stays connected to the server), removes the hosts-file entries (backup
kept) and removes the server's certificate. Each step asks before a Windows prompt, offers to skip, and
only acts if something is there, so it is safe to run any time. **Play** sets everything up again.
`powder --status` and `powder --restore` do the same from a console.

Only the launcher's own changes are undone. Your data (`data/`, the game-data pack) is kept, and game
files are never changed.

## Files

Installed, Powder's folder is `%LOCALAPPDATA%\Powder`; from source, the current folder. In it:
`config.toml` and `launcher.toml` (optional settings), `gamedata/` (the game-data pack), `plugins/`, and
`data/`, which holds everything the server generates on first run: `powder.db` (players), `logs/server.log`,
`bodies/` (logs the game uploads), `tls/` (local CA and server certificate), `nex_signing_key.pem`, and
`launcher/` (launcher log, hosts-file backups, the server's CA as downloaded by the launcher).

## Ports

| Port | Protocol | What                                                                                           |
| ---- | -------- | ---------------------------------------------------------------------------------------------- |
| 443  | TCP      | HTTPS for the game and Ubisoft Connect, and Connect's demux (one listener, routed by hostname) |
| 6005 | UDP      | Quazal NEX (the game's online session)                                                         |
| 80   | TCP      | the local CA's revocation list (`http://public-ubiservices.ubi.com/powder-ca.crl`)             |

All three are set in `config.toml`. By default the server only listens on this PC (`bind = "127.0.0.1"`),
so Windows never asks about the firewall. For friends on a LAN/VPN, set `bind = "0.0.0.0"` and
`public_host` to the server's address there (Windows then asks once whether to allow Powder through the
firewall), and use `server_mode = "remote"` in their launchers.

## Progression

The game computes its progress itself and uploads it as stat events
(`PlayerStats2Protocol::PostEvents_V1`); `services/stats.py` maps each event to the stat it updates
(`EVENT_RULES`). The rules are monotonic (max / add / bitwise-or), so a stale upload can never lower a
level or wipe an unlock. Every upload is also kept raw in `stat_events`. When your own recordings are
present, `tests/test_recordings.py` replays them to check the rules reproduce every recorded change.

## Ubisoft Connect

Steep only goes online when Ubisoft Connect is online, and without Ubisoft's servers Connect starts in
offline mode. So the server also stands in for what Connect needs:

- **Login**: Connect's `POST /v3/profiles/sessions` (remember-me, refresh or email:password; every
  credential is accepted, passwords are never logged or stored) and its other UbiServices calls.
- **Demux** (`dmx.upc.ubisoft.com`): Connect's own protocol for login, owned games, friends, play
  sessions, achievements and ownership tokens (`transport/demux/`). It shares port 443 with HTTPS: the
  listener routes by the requested hostname (`transport/tls_router.py`).
- **Internet check** and CORS for Connect's web UI.
- **Cloud saves** are switched off in what Connect is told about the games (`connect_cloud_saves`), so it
  never prompts to sync.
- **Certificates**: Connect checks them through Windows, so the server generates a local CA in
  `data/tls/` ("Powder (Steep) local CA" in Windows' certificate list), restricted by name constraints to
  `ubi.com` / `ubisoft.com` / loopback, so it cannot vouch for any other website. The launcher installs it;
  by hand:

```
uv run powder-server admin trust install    # current user's trusted roots; Windows asks to confirm
uv run powder-server admin trust status
uv run powder-server admin trust remove
```

What is not emulated (cloud saves, friends, Connect's news and store pages): see [Game features](#game-features).

The server's own endpoints: `GET /_powder/status` (connected Connect clients and game sessions) and
`GET /_powder/ca.pem` (the CA certificate). No authentication yet: meant for a LAN/VPN of friends.

## Admin

All commands work on the default player unless a player is given.

```
uv run powder-server admin players
uv run powder-server admin inventory              # item count + credits
uv run powder-server admin grant credits 50000    # or an item id; negative to remove
uv run powder-server admin stats --key level
uv run powder-server admin ledger                 # recent inventory changes
uv run powder-server admin offers armada          # search the store catalogue
uv run powder-server admin events                 # recent progress uploads from the game
uv run powder-server admin scores                 # personal best per challenge
uv run powder-server admin entities               # rider card, favorites
uv run powder-server admin achievements --all     # earned achievements (and the open ones)
uv run powder-server admin import <recordings>    # your own recorded progress, once
uv run powder-server admin reset                  # the player starts again as a new player
uv run powder-server admin trust status           # the local CA in Windows (also install / remove)
```

## How a request is answered

|     | HTTPS                                                 | NEX                                         |
| --- | ----------------------------------------------------- | ------------------------------------------- |
| 1   | plugin `before_http` hook                             | plugin `before_rmc` hook                    |
| 2   | route with real logic (`transport/https/routes_*.py`) | handler (`transport/nex/handlers/*`)        |
| 3   | game-data pack reply (configuration)                  | game-data pack reply (`rmc.json`)           |
| 4   | `{}` + `UNMATCHED` in the log                         | RMC error `Core/2` + `UNHANDLED` in the log |

## Extending

- **New NEX method**: add a function to a module in `transport/nex/handlers/` (or a new module listed
  in `handlers/__init__.py`): `fn(ctx, params: Reader) -> bytes` (may be `async`). `ctx.profile_id`,
  `ctx.services`, `ctx.conn`. Raise `Fallthrough` to use the pack's reply instead.
- **New HTTPS endpoint**: add a route in `transport/https/routes_*.py`; keep logic in `services/`.
- **Schema change**: add `db/migrations/000N_name.sql`; it is applied on the next start.
- **Plugin**: drop `plugins/name.py` with `setup(api)`; see `powder/server/plugins/api.py`.
- **Several players**: map machines to profiles in `[players.bind]` (client IP -> profile id) and set
  `public_host` to the host's LAN/VPN address. Each player gets their own inventory and stats.

## Licence

Powder is free software under the [GNU Affero General Public License v3.0 or later](LICENSE): you may
use, change and share it; if you run a changed version as a server for others, you must offer them its
source. The licence covers Powder's code only; Steep, its data and the game-data pack belong to Ubisoft.
The installer includes the licences of the bundled libraries (`THIRD_PARTY_LICENSES.txt`).
