"""Cloud saves: Connect's cloudSave entity, upload links from the demux cloudsave_service, the stored files.
Layouts as recorded from Ubisoft's service; ids, names and contents made up."""
import base64
import hashlib

import pytest
from aiohttp.test_utils import TestClient, TestServer

import synthetic as syn
from powder.server.constants import SPACE_ID
from powder.server.transport.demux import pb
from powder.server.transport.demux.services import ServiceContext, answer
from powder.server.transport.https.app import build_app

UBI = {"Host": syn.UBI}
PRODUCT = 3280
NAME = f"upcCloudSaveUplayId_{PRODUCT}"


@pytest.fixture
async def client(services, player):
    c = TestClient(TestServer(build_app(services, nex_public_key="TESTKEY")))
    await c.start_server()
    yield c
    await c.close()


def entity_body(revision=None, sizes=(10, 20)):
    slots = [{"id": i, "filename": str(i), "hash": "0" * 32, "size": n, "lastModified": "2026-01-01T00:00:00.000Z"}
             for i, n in enumerate(sizes, 1)]
    body = {"spaceId": SPACE_ID, "name": NAME, "tags": [], "type": "cloudSave",
            "obj": {"slots": slots, "timestamp": "1700000000", "pc_name": "TEST-PC"}}
    if revision is not None:
        body["revision"] = revision
    return body


def md5(data: bytes) -> str:
    return base64.b64encode(hashlib.md5(data).digest()).decode()


def cloud_links(services, profile_id, items: list, method: int) -> tuple[int, list[tuple[str, str]]]:
    """A CloudsaveUrlReq {1 token, 2 product, 3 method, 4* items} -> (status, [(header text, URL)])."""
    request = pb.encode([(1, [(1, 9), (2, [(1, "ownership-token"), (2, PRODUCT), (3, method), *items])])])
    reply = pb.decode(pb.first(pb.decode(answer(ServiceContext(services, profile_id), "cloudsave_service",
                                                request)), 1))
    body = pb.decode(pb.first(reply, 2))
    assert pb.first(reply, 1) == 9
    out = []
    for _, raw, _ in [f for f in body if f[0] == 2]:
        e = pb.decode(raw)
        out.append((pb.text(e, 1), pb.text(e, 2)))
    return pb.first(body, 1), out


def download_links(services, profile_id, slots: list[str]):
    return cloud_links(services, profile_id, [(4, [(1, s)]) for s in slots], 2)


def upload_links(services, profile_id, files: dict[str, bytes]) -> list[tuple[str, str]]:
    """The demux request Connect sends at game exit (method Put) -> (headers text, URL) per slot."""
    slots = [(4, [(1, name), (2, [(1, len(data)), (2, md5(data))])]) for name, data in files.items()]
    status, out = cloud_links(services, profile_id, slots, 1)
    assert status == 1
    return out


async def test_a_save_goes_up_and_comes_back(client, services):
    me = services.default_player_id()
    url = f"/v1/profiles/{me}/entities?name={NAME}"
    assert (await (await client.get(url, headers=UBI)).json()) == {"entities": []}
    # game exit: the entity first (creating it), then links, then the files
    created = await (await client.post(f"/v1/profiles/{me}/entities", headers=UBI, json=entity_body())).json()
    assert created["name"] == NAME and created["type"] == "cloudSave" and created["revision"] == 1
    assert (await (await client.get(url, headers=UBI)).json()) == {"entities": []}      # files not there yet
    files = {"1": b"first slot", "2": b"second slot!"}
    links = upload_links(services, me, files)
    assert links[0][0] == f"Content-Length: 10\nContent-MD5: {md5(files['1'])}\n"
    for (_, link), data in zip(links, files.values()):
        path = link.split(".com", 1)[1]
        r = await client.put(path, data=data, headers={**UBI, "Content-MD5": md5(data)})
        assert r.status == 200
    (offered,) = (await (await client.get(url, headers=UBI)).json())["entities"]
    assert offered["entityId"] == created["entityId"] and [s["filename"] for s in offered["obj"]["slots"]] == ["1", "2"]
    # next exit: PUT with the current revision
    put = await (await client.put(f"/v1/profiles/entities/{created['entityId']}", headers=UBI,
                                  json=entity_body(revision=1))).json()
    assert put["revision"] == 2
    # another PC asks for download links (method Get) and downloads
    status, got = download_links(services, me, ["1", "2"])
    assert status == 1 and [h for h, _ in got] == ["", ""]
    for (_, link), data in zip(got, files.values()):
        r = await client.get(link.split(".com", 1)[1], headers=UBI)
        assert r.status == 200 and await r.read() == data
    assert (await client.put(got[0][1].split(".com", 1)[1], data=b"x", headers=UBI)).status == 403  # GET only
    assert download_links(services, me, ["3"])[0] == 2                                # not stored: an error
    # deleting (method Delete)
    status, got = cloud_links(services, me, [(4, [(1, "2")])], 3)
    assert status == 1 and (await client.delete(got[0][1].split(".com", 1)[1], headers=UBI)).status == 204
    assert not services.cloudsaves.path(me, PRODUCT, "2").exists()


async def test_a_damaged_upload_never_replaces_a_stored_file(client, services):
    me = services.default_player_id()
    (_, link), = upload_links(services, me, {"1": b"good data"})
    path = link.split(".com", 1)[1]
    assert (await client.put(path, data=b"good data", headers=UBI)).status == 200     # MD5 from the request
    (_, link), = upload_links(services, me, {"1": b"newer data"})
    r = await client.put(link.split(".com", 1)[1], data=b"newer da", headers=UBI)     # cut off
    assert r.status == 400
    assert services.cloudsaves.path(me, PRODUCT, "1").read_bytes() == b"good data"


async def test_links_and_entities_belong_to_one_player(client, services):
    me = services.default_player_id()
    services.accounts.register("host@example.com", "correct horse", "")             # takes over `me`
    other = services.accounts.register("guest@example.com", "correct horse", "Guest").profile_id
    (_, link), = upload_links(services, other, {"1": b"guest save"})
    path, token = link.split(".com", 1)[1].split("?t=")
    assert (await client.put(f"{path.rsplit('/', 1)[0]}/2?t={token}", data=b"x", headers=UBI)).status == 403
    assert (await client.get(f"/_powder/cloudsaves/{PRODUCT}/1?t=made-up", headers=UBI)).status == 403
    services.entities.put("00000000-0000-4000-8000-0000000000aa", {**entity_body(), "type": "cloudSave"}, other)
    r = await client.put("/v1/profiles/entities/00000000-0000-4000-8000-0000000000aa", headers=UBI,
                         json=entity_body(revision=1))
    assert r.status == 403                                                            # not the requester's
    assert (await (await client.get(f"/v1/profiles/{other}/entities?name={NAME}", headers=UBI)).json()) == \
        {"entities": []}


def test_ownership_token_and_signature(services, player):
    """Connect asks for these (e.g. before a cloud save sync); definitions from Connect's mg.protocol.ownership."""
    ctx = ServiceContext(services, services.default_player_id())
    for kind, reply_kind in ((28, 27), (26, 25)):
        reply = pb.decode(pb.first(pb.decode(answer(ctx, "ownership_service",
                                                    pb.encode([(1, [(1, 5), (kind, [(1, PRODUCT)])])]))), 1))
        body = pb.decode(pb.first(reply, reply_kind))
        assert pb.first(body, 1) == 1 and pb.first(body, 3) > 0


def test_admin_listing_and_deleting(services, player):
    me = services.default_player_id()
    services.entities.put("00000000-0000-4000-8000-0000000000bb", {**entity_body(sizes=(5,)), "type": "cloudSave"}, me)
    link = services.cloudsaves.resolve(services.cloudsaves.link(me, PRODUCT, "1"), PRODUCT, "1")
    services.cloudsaves.store(link, b"12345", None)
    (item,) = services.cloudsaves.all(me)
    assert item["product"] == PRODUCT and item["complete"]
    assert services.cloudsaves.delete(me) == 1
    assert services.cloudsaves.all(me) == [] and not services.cloudsaves.path(me, PRODUCT, "1").exists()
