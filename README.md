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
| Ubisoft Connect sign-in                         | ✅     | With your Powder account (registered in the launcher); Connect stays signed in              |
| Game login (Steam and Ubisoft Connect versions) | ✅     | Both versions of the final update (TU7)                                                     |
| Owned games / DLC ownership                     | ✅     | Steep and its add-ons, built in                                                             |
| Game configuration, season info                 | ✅     | Built in (the game-data pack's copy is used when installed)                                 |
| Several players on one server                   | ✅     | An account per player (email and password), each with their own progress                    |

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
| Leaderboards (challenge rankings) | ✅     | The server's players, by their best score; weekly and daily boards show the bests set in that period |
| Replays / ghosts of other players | 🟡     | Saved and shared rides, photos (likes, deleting) are kept and seen by the other players; challenge ghosts: the game never creates one yet |
| Player-made trails / challenges   | ❌     | Always an empty list                                                        |
| Seasons and tournaments           | 🟡     | No season running, as on Ubisoft's servers at the end; nothing changes      |

### Playing with others

| Feature                                                  | Status | Notes                                                                     |
| -------------------------------------------------------- | ------ | ------------------------------------------------------------------------- |
| Multiplayer sessions (other riders in the world, groups) | ✅     | On a local network (tested with two PCs): riders in the same area meet; groups by pressing G next to each other or "Join group" in the social tab; spectating |
| Matchmaking                                              | ✅     | Players in the same world and area (cell) are put into one session; no matchmaking map, the game splits areas itself |
| Friends list, invites, blocks                            | 🟡     | Every other player of the server is a friend (online when their game or Connect is connected). Invites are refused: accepting needs Ubisoft Connect's own pages; no blocks |
| Other players' profiles (rider card, level)              | ✅     | Steep's profile view of a friend                                          |

### Ubisoft services around the game

| Feature                                                           | Status | Notes                                                                                   |
| ----------------------------------------------------------------- | ------ | --------------------------------------------------------------------------------------- |
| Ubisoft Connect window and in-game overlay (Shift+F2)             | 🟡     | With the game-data pack: Ubisoft's pages; without it: a Powder page of its own            |
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
2. Start **Powder** and press **Play**. It walks you through what it needs to set up (every Windows
   prompt is explained before it appears). If you have a game-data pack, add it once with **Choose
   pack…**; a checkbox then switches it on and off.

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

Ubisoft Connect's window and in-game overlay are a frame from Ubisoft's CDN that loads its pages from the
addresses its settings list. With a pack, those are Ubisoft's pages; without one, the server lists a
single page of its own (`/_powder/overlay/powder.js`), so Connect shows a Powder page instead of its
library and store. Settings Connect still holds in its browser cache are never overwritten (the server
answers "not modified"). If Connect has never been online on a PC and fails with dolphin-032, start it
once normally (without Powder: **Restore system**, sign in to your real account, then Play again).

The pack travels as one `.zip`: the launcher installs it with **Choose pack…** (checking format and file
hashes before using it), and the same works from a console:

```
uv run powder-server gamedata zip steep-gamedata.zip            # the configured pack -> one .zip
uv run powder-server gamedata install steep-gamedata.zip        # .zip or folder -> the configured pack
```

## Players

Every player has an account on the server: an email and a password, registered in the launcher (the
**Account** row or the Account step of Play) and used to sign in to Ubisoft Connect. The email can be
changed later; the password is stored only as a scrypt hash, never logged. Wrong passwords are
rate-limited per address. The first account on a server takes over the player already there (e.g. your
imported progress); every further account starts like a new Ubisoft account: level 1, empty inventory,
`starting_credits`. A server without any account accepts every sign-in, as before accounts existed, until
the first player registers. If someone forgets their password, the host sets a new one:

```
uv run powder-server admin accounts                    # email -> player
uv run powder-server admin account-password <email>    # asks for the new password
uv run powder-server admin account-email <email> <new email>
uv run powder-server admin account-link <email> <player id>   # the account owns that player instead
```

To continue with your own progress, import it once from your recordings (then register: the first account
takes it over):

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
these steps; each step only checks unless something is missing.

Where the server is, is chosen in the **Server** row (remembered, applied at the next Play):

- **Just this PC**: the launcher runs a server that only this PC can use.
- **Host for others**: the launcher runs the server for the whole local network and shows this PC's
  address (choose one if the PC has several). The other players enter it under Join.
- **Join**: the address of a PC that hosts. Nothing runs here but the game.

Without a choice, `server_mode` and `server_address` in `launcher.toml` decide. The steps:

1. **Game files**: finds the chosen Steep, Ubisoft Connect (and Steam for the Steam version), and checks
   that `Steep.exe` is a build the TLS helper supports.
2. **Firewall** (hosting or joining): Windows Firewall must let the other PCs in. The host needs a rule
   for Powder's server program, every player one for Steep itself (the games connect to each other
   directly). Rules are per program, since Windows asks about any program that listens without one. Both
   allow only the local network. If one is missing, the launcher explains it and adds it after you press
   the button (Windows asks for admin rights), before the server starts listening. It also warns if a rule
   from an earlier Windows prompt blocks Steep or Powder.
3. **Server**: reaches it, or starts it locally (Just this PC / Host) after checking that its ports are
   free (if not, it names the program using them), with or without the game-data pack as the checkbox says
   (a server it started with another setting is restarted). Joining, it uses the server on the other PC
   (its pack is that server's business), and warns if that server's major version differs.
4. **DNS**: every Ubisoft hostname (`powder/server/hostnames.py`) must resolve to the server address. If not,
   it shows the lines to add to your own DNS server, or adds them to the hosts file for you (the previous
   hosts file is backed up to `data/launcher/hosts-backups/`).
5. **Certificate**: gets the server's CA and, if Windows doesn't trust it yet, installs it for your user.
6. **Account**: registers your account on the server, or signs in to one you already have there (the
   launcher remembers the email, never the password).
7. **Ubisoft Connect**: restarts it if it is not signed in to the server as this PC's player, then waits
   for its login: sign in with the account's email and password.
8. **Steep**: starts it through Steam or Ubisoft Connect, attaches the TLS helper to the game and waits
   until it is online. Both versions start a short-lived starter process first; the helper attaches only
   to the real game from the chosen install. While the launcher stays open, the server keeps running, so
   Ubisoft Connect stays signed in and the next Play goes straight to the game.

The game-data pack is set up apart from Play, in the **Game data** row under the buttons: **Choose
pack…** installs a `.zip` into the local server's `gamedata/`. Then a checkbox switches it on and off
(remembered in `data/launcher/settings.json`), **Replace…** installs another one and **Remove** deletes
it; changes apply at the next Play.

Every Windows prompt is announced first: the launcher says why it is needed (the hosts file is a protected
system file and firewall rules are system settings, so both need admin rights; Windows asks to confirm a
new certificate) and only continues when you press the button for it.

The TLS helper (`native/`) makes the game accept the server's certificate: `hook_guard.dll` traps the
game's certificate check without changing code bytes at first, `armer.exe` keeps that trap armed and then
patches the check once the game's integrity check is over. It only fits the `Steep.exe` builds listed in
`powder/launcher/game.py` (the Steam and the Ubisoft Connect download of the final update: the same
compiled game, packaged differently). Game files are never modified on disk.

### Restoring your PC

**Restore system…** undoes everything the launcher set up: it stops the server it started, quits
Ubisoft Connect (which otherwise stays connected to the server), removes the hosts-file entries (backup
kept), the server's certificate and the launcher's firewall rules (group "Powder"). Each step asks before a Windows prompt, offers to skip, and
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
| 11000-11003, 11005 | UDP | Storm, the games' peer-to-peer helpers: NAT detection (11000-11003) and traversal (11005) |

All of them are set in `config.toml` (`storm_port` is the first of the Storm ports). By default the server only listens on this PC (`bind = "127.0.0.1"`).
To host for the local network, choose **Host for others** in the launcher: it starts the server with
`--bind 0.0.0.0 --public-host <this PC's address>` and adds the firewall rule. Without the launcher, set
`bind = "0.0.0.0"` and `public_host` in `config.toml` and allow the server in Windows Firewall yourself.

The players' games also talk to each other directly (UDP, peer-to-peer, ports the game picks). On a
local network that needs nothing but the firewall rule for Steep. The game only starts its multiplayer
part when its configuration names peer-to-peer helper servers (Storm), and it finds the other players'
games through them: NAT detection tells a game the address the server sees, and the traversal server
passes a game's "connect me to that player" on to the other game, so both send to each other at once
(hole punching). Powder runs both itself (`powder/server/transport/storm.py`, as recorded from
Ubisoft's) and tells the game they are at the server's address (`[multiplayer] storm = "powder"`, the
default; `"off"` switches the game's multiplayer off). No game-data pack is needed for it.

## Progression

The game computes its progress itself and uploads it as stat events
(`PlayerStats2Protocol::PostEvents_V1`); `services/stats.py` maps each event to the stat it updates
(`EVENT_RULES`). The rules are monotonic (max / add / bitwise-or), so a stale upload can never lower a
level or wipe an unlock. Every upload is also kept raw in `stat_events`. When your own recordings are
present, `tests/test_recordings.py` replays them to check the rules reproduce every recorded change.

## Ubisoft Connect

Steep only goes online when Ubisoft Connect is online, and without Ubisoft's servers Connect starts in
offline mode. So the server also stands in for what Connect needs:

- **Login**: Connect's `POST /v3/profiles/sessions` (email and password of a Powder account, then the
  remember-me ticket it got; passwords are never logged) and its other UbiServices calls. The game's own
  login follows the player Connect signed in as on the same PC.
- **Demux** (`dmx.upc.ubisoft.com`): Connect's own protocol for login, owned games, friends, play
  sessions, achievements and ownership tokens (`transport/demux/`). It shares port 443 with HTTPS: the
  listener routes by the requested hostname (`transport/tls_router.py`).
- **Internet check** and CORS for Connect's web UI.
- **Cloud saves** (`connect_cloud_saves`): Connect uploads the game's own save files at game exit and gets
  them back at the start on another PC (`services/cloudsaves.py`, files in `data/cloudsaves/`). A cloud save
  is only offered while all its files are there; `powder-server admin cloud-saves [PLAYER] --delete`
  removes one.
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

The server's own endpoints: `GET /_powder/status` (connected Connect clients and game sessions, and the
caller's address), `GET /_powder/ca.pem` (the CA certificate) and `/_powder/account/*` (the launcher
registers and changes accounts there; every call carries the account's password). Meant for a local
network of friends: don't expose the server to the internet.

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
- **Matchmaking** policy (who meets whom) is in `services/matchmaking.py`; the game's calls and the
  notifications it expects are described there.

## Licence

Powder is free software under the [GNU Affero General Public License v3.0 or later](LICENSE): you may
use, change and share it; if you run a changed version as a server for others, you must offer them its
source. The licence covers Powder's code only; Steep, its data and the game-data pack belong to Ubisoft.
The installer includes the licences of the bundled libraries (`THIRD_PARTY_LICENSES.txt`).
