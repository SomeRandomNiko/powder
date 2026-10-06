"""The Ubisoft hostnames the game and Ubisoft Connect must reach on this server.

REDIRECT_HOSTS must all resolve to the server's address (hosts file or a DNS server); the launcher checks
exactly this list. CERT_NAMES are what the server certificate covers (the redirected names plus wildcards
for hosts we do not redirect yet but answer if a user does).
"""

GAME_HOSTS = [
    "public-ubiservices.ubi.com",
    "msr-public-ubiservices.ubi.com",
    "public-im-ubiservices.ubi.com",
    "public-ws-ubiservices.ubi.com",
    "steep-prod-pc.steep.olympus.ubi.com",
]
CONNECT_HOSTS = [
    "dmx.upc.ubisoft.com",            # demux: Connect's login / owned games / play sessions
    "channel-service.upc.ubi.com",
    "static3.cdn.ubi.com",            # Connect's internet check
]
REDIRECT_HOSTS = GAME_HOSTS + CONNECT_HOSTS

DEMUX_HOST = "dmx.upc.ubisoft.com"
CRL_HOST = "public-ubiservices.ubi.com"   # serves the local CA's CRL over plain HTTP (port 80)

CERT_NAMES = REDIRECT_HOSTS + [
    "*.ubiservices.ubi.com",
    "*.olympus.ubi.com",
    "*.upc.ubi.com",
    "*.upc.ubisoft.com",
    "*.cdn.ubi.com",
    "*.ubi.com",
    "*.ubisoft.com",
]
