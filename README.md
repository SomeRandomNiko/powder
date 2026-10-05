# steepserver — offline server for Steep

One process that stands in for Ubisoft's online services: the UbiServices HTTPS API (all
`*.ubi.com` hosts, port 443), the Quazal NEX/RendezVous server (UDP 6005) and what Ubisoft Connect
needs to stay online (its demux protocol, see below). It answers from
recordings of real sessions, keeps per-player state in SQLite, and is built to grow: several
players, gameplay toggles, plugins, admin tools.

What persists per player: inventory and credits (store purchases, challenge rewards), progression
(level and XP per sport, playtime, completed memorables, unlocked dropzones and challenges, from the
game's stat uploads), personal best per challenge, and profile entities (rider card, favorites).

## Progression

The game computes its progress itself and uploads it as stat events
(`PlayerStats2Protocol::PostEvents_V1`); `services/stats.py` maps each event to the stat it updates
(`EVENT_RULES`). The rules were derived by diffing recorded stat snapshots against the events uploaded
between them, and `tests/test_recordings.py` replays your recorded sessions to check they reproduce
every recorded change exactly. They are monotonic (max / add / bitwise-or), so a stale upload can never
lower a level or wipe an unlock. Every upload is also kept raw in `stat_events`.

## Run

```
cd server
uv run steepserver run                  # uses ./config.toml if present (see config.example.toml)
uv run pytest                           # tests (codecs vs captured bytes, state, HTTPS, NEX over UDP)
```

The game reaches the server through the hosts file (`tools\redirect.ps1 -On`, self-elevating) and
accepts our certificate thanks to the in-process hook (`tools\inject.ps1` + `tools\armer.exe`).
Full live procedure: `notes\SESSION-HANDOFF.md`.

Runtime files go to `data/` (`steep.db`, `logs/server.log`, `bodies/` = logs the game uploads,
`tls/server.pem` and `nex_signing_key.pem`, both generated on first run).

## Ubisoft Connect

Steep only goes online when Ubisoft Connect is online, and without Ubisoft's servers Connect starts in
offline mode. So the server also stands in for what Connect needs:

- **Login**: Connect's `POST /v3/profiles/sessions` (remember-me, refresh or email:password; every
  credential is accepted, passwords are never logged or stored) and its other UbiServices calls,
  answered from `recordings/connect-fixtures.json`.
- **Demux** (`dmx.upc.ubisoft.com`, mapped to `127.0.0.2:443` by the hosts file): Connect's own
  protocol for login, owned games, play sessions and ownership tokens
  (`transport/demux/`, replies from `recordings/demux.json`).
- **Internet check** (`static3.cdn.ubi.com/.../test_internet.txt`) and CORS for Connect's web UI.
- **Certificates**: Connect checks them through Windows, so the server generates a local CA in
  `data/tls/`, restricted by name constraints to `ubi.com` / `ubisoft.com` / loopback, and serves its
  revocation list on `http://127.0.0.1/steep-ca.crl`. Trust it once:

```
uv run steepserver admin trust install    # current user's trusted roots; Windows asks to confirm
uv run steepserver admin trust status
uv run steepserver admin trust remove
```

Not emulated yet: cloud saves (Connect's "failed to synchronize game save": choose Skip), the friends
list and the store/news pages of Connect's UI.

## Recordings (your data, never committed)

`recordings/` holds replies recorded from your own online sessions: `fixtures.json` (HTTPS),
`fixtures-rmc.json` (NEX), `inventory-updates.json`, `connect-fixtures.json` and `demux.json`
(Ubisoft Connect), and optionally `sessions/*.jsonl` (decrypted
NEX sessions, used by `tests/test_recordings.py`). They contain personal data (your account,
friends, other players on leaderboards), so the folder is gitignored and nothing in the repository
may depend on it. Without recordings the server still runs; it simply has nothing to replay. The
account the recordings belong to becomes the default player and is seeded from them on first run.
Tests use a made-up set (`tests/synthetic.py`); `tests/test_recordings.py` additionally checks your
local recordings when present, asserting only generic properties.

## Admin

```
uv run steepserver admin players
uv run steepserver admin inventory              # item count + credits of the default player
uv run steepserver admin grant credits 50000
uv run steepserver admin stats --key level
uv run steepserver admin ledger
uv run steepserver admin offers armada          # search the store catalogue
uv run steepserver admin events                 # recent progress uploads from the game
uv run steepserver admin scores                 # personal best per challenge
uv run steepserver admin entities               # rider card, favorites
uv run steepserver admin reseed                 # back to the recorded state
```

## How a request is answered

| | HTTPS | NEX |
|---|---|---|
| 1 | plugin `before_http` hook | plugin `before_rmc` hook |
| 2 | route with real logic (`transport/https/routes_ubi.py`) | handler (`transport/nex/handlers/*`) |
| 3 | recorded reply (`recordings/fixtures.json`) | recorded reply (`recordings/fixtures-rmc.json`) |
| 4 | `{}` + `UNMATCHED` in the log | RMC error `Core/2` + `UNHANDLED` in the log |

Recordings come from real online sessions captured with the reverse-engineering tooling of the
parent project (not part of this repository). Only calls whose answer depends on the player or on
live ids get code; everything else is the real server's bytes.

## Layout

```
steepserver/
  app.py              starts HTTPS + NEX on one event loop
  config.py           config.toml -> Config; gameplay toggles per player
  constants.py        game-wide ids: app, space, credits item
  tls.py              local CA (name-constrained) + server certificate + CRL (generated on first run)
  fixtures/           lookups over the recordings; catalog.py = store offers
  core/               identity (who logs in), sessions (tickets, live connections, server push), events
  db/                 SQLite, numbered migrations, repositories
  services/           game logic: accounts, economy, stats (progression), entities, matchmaking, seed
  transport/https/    aiohttp app, UbiServices routes, notification WebSocket, CRL site
  transport/demux/    Ubisoft Connect's demux protocol (TLS, protobuf, per-service replies)
  transport/nex/      PRUDP, secure channel, RMC codec, dispatcher, per-protocol handlers
  plugins/            plugin API + loader
  admin/              CLI
plugins/              your plugins (_example.py shows the API)
recordings/           your recorded replies (gitignored)
tests/                pytest; synthetic.py = made-up recordings used by the tests
```

## Extending

- **New NEX method**: add a function to a module in `transport/nex/handlers/` (or a new module listed
  in `handlers/__init__.py`): `fn(ctx, params: Reader) -> bytes` (may be `async`). `ctx.profile_id`,
  `ctx.services`, `ctx.conn`. Raise `Fallthrough` to use the recording instead.
- **New HTTPS endpoint**: add a route in `transport/https/routes_ubi.py`; keep logic in `services/`.
- **Schema change**: add `db/migrations/000N_name.sql`; it is applied on the next start.
- **Plugin**: drop `plugins/name.py` with `setup(api)`; see `steepserver/plugins/api.py`.
- **Several players**: map machines to profiles in `[players.bind]` (client IP -> profile id) and set
  `public_host` to the host's LAN/VPN address. Each player gets their own inventory and stats.
