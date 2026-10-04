# steepserver — offline server for Steep

One process that stands in for Ubisoft's online services: the UbiServices HTTPS API (all
`*.ubi.com` hosts, port 443) and the Quazal NEX/RendezVous server (UDP 6005). It answers from
recordings of real sessions, keeps per-player state (inventory, credits, stats) in SQLite, and
is built to grow: several players, gameplay toggles, plugins, admin tools.

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

## Recordings (your data, never committed)

`recordings/` holds replies recorded from your own online sessions: `fixtures.json` (HTTPS),
`fixtures-rmc.json` (NEX), `inventory-updates.json`. They contain personal data (your account,
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
  tls.py              self-signed certificate for the Ubisoft hostnames (generated on first run)
  fixtures/           lookups over the recordings; catalog.py = store offers
  core/               identity (who logs in), sessions (tickets, live connections, server push), events
  db/                 SQLite, numbered migrations, repositories
  services/           game logic: accounts, economy, stats, matchmaking, seed
  transport/https/    aiohttp app, UbiServices routes, notification WebSocket
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
