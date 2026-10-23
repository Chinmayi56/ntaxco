"""Share a booking with a partner: invite link, partner login gate, per-partner access grants, revoke.

Runs the real FastAPI app in-process on mongomock-motor.
Run:  cd backend && python -m pytest -c /dev/null --rootdir=. tests/test_partner_share.py
"""
import asyncio
import os
import sys
import uuid
from pathlib import Path

import pytest

pytest.importorskip("mongomock_motor")
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "ntaxco_partner_share_test")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123")
os.environ.pop("NTAXCO_ENABLE_DEMO_SEED", None)

import motor.motor_asyncio as _motor  # noqa: E402
_motor.AsyncIOMotorClient = lambda *a, **k: AsyncMongoMockClient()

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402
import partner_share  # noqa: E402

LOOP = asyncio.new_event_loop()


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:
        yield c


@pytest.fixture(scope="module")
def admin(client):
    email, pw = f"admin-{uuid.uuid4().hex[:6]}@example.com", "AdminPass#123"
    LOOP.run_until_complete(server.db.users.insert_one({
        "id": f"ADMIN-{uuid.uuid4().hex[:6].upper()}", "name": "Test Admin", "email": email, "role": "admin",
        "password_hash": server.hash_password(pw), "status": "active", "meta": {}}))
    r = client.post("/api/auth/admin/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def _register(client, name, mobile):
    email = f"{name.lower()}.{uuid.uuid4().hex[:5]}@example.com"
    r = client.post("/api/auth/register", json={"full_name": name, "email": email, "password": "CustPass#123",
                                                 "role": "customer", "mobile": mobile})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}, email


@pytest.fixture(scope="module")
def world(client, admin):
    svc = client.post("/api/services", headers=admin, json={
        "name": "GST Filing", "title": "GST Filing", "category": "GST", "description": "x", "final_price": 1000,
        "status": "Active"}).json()["data"]
    # Unique mobiles: other test modules share the in-process mock DB and customers are matched by mobile.
    a_h, a_mail = _register(client, "Alpha", "9" + str(uuid.uuid4().int)[:9])
    b_h, b_mail = _register(client, "Bravo", "9" + str(uuid.uuid4().int)[:9])
    r = client.post("/api/bookings", headers=a_h, json={"service_id": svc["id"], "service": svc["name"],
                                                        "email": a_mail, "client_request_id": uuid.uuid4().hex})
    assert r.status_code == 200, r.text
    return dict(a_h=a_h, b_h=b_h, booking=r.json()["data"], admin=admin)


def _url(booking_id):
    return f"/api/bookings/{booking_id}/partner-share"


def test_requires_login(client, world):
    assert client.post(_url(world["booking"]["id"])).status_code in (401, 403)


def test_owner_generates_and_link_is_saved(client, world):
    bid = world["booking"]["id"]
    assert client.get(_url(bid), headers=world["a_h"]).json()["data"] is None
    r = client.post(_url(bid), headers=world["a_h"], json={})
    assert r.status_code == 200, r.text
    share = r.json()["data"]
    assert share["booking_id"] == bid
    assert len(share["token"]) >= 43 and share["link_path"] == f"/partner/booking/{share['token']}"
    # persisted in MongoDB with the booking relationship
    row = LOOP.run_until_complete(server.db["erp_booking_partner_shares"].find_one({"token": share["token"]}))
    assert row and row["booking_id"] == bid and row["customer_id"] == world["booking"]["customer_id"]
    # reload returns the same link
    again = client.get(_url(bid), headers=world["a_h"]).json()["data"]
    assert again["token"] == share["token"]
    world["share"] = share


def test_generate_is_idempotent_and_regenerate_rotates(client, world):
    bid = world["booking"]["id"]
    same = client.post(_url(bid), headers=world["a_h"], json={}).json()["data"]
    assert same["token"] == world["share"]["token"]
    new = client.post(_url(bid), headers=world["a_h"], json={"regenerate": True}).json()["data"]
    assert new["token"] != world["share"]["token"]
    # the old token stops working immediately
    assert client.get(f"/api/public/partner-share/{world['share']['token']}").status_code == 410
    world["share"] = new


def test_other_customer_cannot_use_or_see_it(client, world):
    bid = world["booking"]["id"]
    assert client.post(_url(bid), headers=world["b_h"], json={}).status_code == 404
    assert client.get(_url(bid), headers=world["b_h"]).status_code == 404
    assert client.delete(_url(bid), headers=world["b_h"]).status_code == 404
    assert client.post(_url("BKG-DOESNOTEXIST"), headers=world["a_h"], json={}).status_code == 404


def test_public_link_status_exposes_no_booking_data(client, world):
    token = world["share"]["token"]
    r = client.get(f"/api/public/partner-share/{token}")  # no auth header: only tells the login gate the link is live
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data == {"valid": True, "requires_login": True, "restricted": False}


def test_bad_tokens_rejected(client):
    assert client.get("/api/public/partner-share/short").status_code == 404
    assert client.get("/api/public/partner-share/" + "A" * 43).status_code == 404
    assert client.get("/api/public/partner-share/" + "A" * 40 + "%24ne").status_code == 404  # operator-style junk
    assert client.get("/api/public/partner-share/" + "A" * 100).status_code == 404         # too long


def test_expired_link_is_gone(client, world):
    token = world["share"]["token"]
    LOOP.run_until_complete(server.db["erp_booking_partner_shares"].update_one(
        {"token": token}, {"$set": {"expires_at": "2000-01-01T00:00:00+00:00"}}))
    assert client.get(f"/api/public/partner-share/{token}").status_code == 410
    # an expired link is replaced by a fresh one on the next generate
    fresh = client.post(_url(world["booking"]["id"]), headers=world["a_h"], json={}).json()["data"]
    assert fresh["token"] != token
    world["share"] = fresh


def test_revoke(client, world):
    bid, token = world["booking"]["id"], world["share"]["token"]
    assert client.delete(_url(bid), headers=world["a_h"]).status_code == 200
    assert client.get(f"/api/public/partner-share/{token}").status_code == 410
    assert client.get(_url(bid), headers=world["a_h"]).json()["data"] is None


def test_existing_booking_apis_unchanged(client, world):
    bid = world["booking"]["id"]
    assert client.get(f"/api/bookings/{bid}", headers=world["a_h"]).status_code == 200
    assert [b["id"] for b in client.get("/api/bookings", headers=world["a_h"]).json()["data"]] == [bid]


# ----------------------------------------------------------------------------------------------
# Partner login + per-partner authorization
# ----------------------------------------------------------------------------------------------
REDEEM = "/api/partner/shares/redeem"
MINE = "/api/partner/shared-bookings"


@pytest.fixture()
def fresh(client, world):
    """A brand-new unclaimed invite link for Alpha's booking, plus two independent partner accounts."""
    partner_share._redeem_hits.clear()  # in-process IP limiter: every test shares one client IP
    partner_share._hits.clear()
    bid = world["booking"]["id"]
    client.delete(_url(bid), headers=world["a_h"])  # reset: kills links + grants from earlier tests
    link = client.post(_url(bid), headers=world["a_h"], json={}).json()["data"]
    p1, p1_mail = _register(client, "Partner1", "9" + str(uuid.uuid4().int)[:9])
    p2, p2_mail = _register(client, "Partner2", "9" + str(uuid.uuid4().int)[:9])
    return dict(bid=bid, link=link, p1=p1, p1_mail=p1_mail, p2=p2, p2_mail=p2_mail)


def test_redeem_requires_login(client, fresh):
    assert client.post(REDEEM, json={"token": fresh["link"]["token"]}).status_code in (401, 403)
    assert client.get(MINE).status_code in (401, 403)


def test_partner1_gets_access_and_sees_only_safe_fields(client, world, fresh):
    r = client.post(REDEEM, headers=fresh["p1"], json={"token": fresh["link"]["token"]})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["is_owner"] is False and d["access_id"].startswith("PAC-") and d["service"] == "GST Filing"
    leaked = {"id", "booking_id", "customer_id", "email", "mobile", "payment_status", "estimated_fee",
              "price_snapshot", "documents", "token", "customer_email", "phone"}
    assert not (leaked & set(d)), set(d)
    # persisted in MongoDB against partner 1's account
    row = LOOP.run_until_complete(server.db["erp_booking_partner_access"].find_one({"id": d["access_id"]}))
    assert row["booking_id"] == fresh["bid"] and row["partner_email"] == fresh["p1_mail"].lower()
    # listing + detail work for partner 1
    assert [b["access_id"] for b in client.get(MINE, headers=fresh["p1"]).json()["data"]] == [d["access_id"]]
    assert client.get(f"{MINE}/{d['access_id']}", headers=fresh["p1"]).json()["data"]["booking_no"] == d["booking_no"]
    # redeeming again is idempotent
    again = client.post(REDEEM, headers=fresh["p1"], json={"token": fresh["link"]["token"]})
    assert again.status_code == 200 and again.json()["data"]["access_id"] == d["access_id"]
    # the owner sees who has access
    owners = client.get(f"/api/bookings/{fresh['bid']}/partner-access", headers=world["a_h"]).json()["data"]
    assert [o["access_id"] for o in owners] == [d["access_id"]]


def test_partner2_cannot_see_partner1_booking(client, fresh):
    d = client.post(REDEEM, headers=fresh["p1"], json={"token": fresh["link"]["token"]}).json()["data"]
    # no grant -> empty list, and partner 1's access id is a 404 for partner 2 (backend check, not UI)
    assert client.get(MINE, headers=fresh["p2"]).json()["data"] == []
    assert client.get(f"{MINE}/{d['access_id']}", headers=fresh["p2"]).status_code == 404
    # partner 2 cannot reuse partner 1's already-claimed link
    r = client.post(REDEEM, headers=fresh["p2"], json={"token": fresh["link"]["token"]})
    assert r.status_code == 403, r.text
    assert client.get(MINE, headers=fresh["p2"]).json()["data"] == []
    # and the booking itself stays invisible through the normal customer APIs
    assert client.get(f"/api/bookings/{fresh['bid']}", headers=fresh["p2"]).status_code in (403, 404)
    assert client.get(f"/api/bookings/{fresh['bid']}", headers=fresh["p1"]).status_code in (403, 404)
    assert client.get(_url(fresh["bid"]), headers=fresh["p1"]).status_code == 404  # partner cannot manage sharing


def test_partner2_authorized_separately_with_own_link(client, world, fresh):
    d1 = client.post(REDEEM, headers=fresh["p1"], json={"token": fresh["link"]["token"]}).json()["data"]
    link2 = client.post(_url(fresh["bid"]), headers=world["a_h"], json={}).json()["data"]
    assert link2["token"] != fresh["link"]["token"]  # claimed link is consumed; a new one is issued
    d2 = client.post(REDEEM, headers=fresh["p2"], json={"token": link2["token"]})
    assert d2.status_code == 200, d2.text
    d2 = d2.json()["data"]
    assert d2["access_id"] != d1["access_id"]
    assert [b["access_id"] for b in client.get(MINE, headers=fresh["p2"]).json()["data"]] == [d2["access_id"]]
    # partner 2's grant does not open partner 1's id and vice versa
    assert client.get(f"{MINE}/{d1['access_id']}", headers=fresh["p2"]).status_code == 404
    assert client.get(f"{MINE}/{d2['access_id']}", headers=fresh["p1"]).status_code == 404


def test_email_restricted_link(client, world, fresh):
    bid = fresh["bid"]
    link = client.post(_url(bid), headers=world["a_h"], json={"partner_email": fresh["p2_mail"].upper()}).json()["data"]
    assert link["partner_email"] == fresh["p2_mail"].lower() and link["token"] != fresh["link"]["token"]
    assert client.get(f"/api/public/partner-share/{link['token']}").json()["data"]["restricted"] is True
    # the wrong account is refused and does NOT burn the link
    assert client.post(REDEEM, headers=fresh["p1"], json={"token": link["token"]}).status_code == 403
    assert client.get(MINE, headers=fresh["p1"]).json()["data"] == []
    # the invited account gets in
    assert client.post(REDEEM, headers=fresh["p2"], json={"token": link["token"]}).status_code == 200
    # invalid partner email is rejected
    assert client.post(_url(bid), headers=world["a_h"], json={"partner_email": "not-an-email"}).status_code == 422


def test_owner_opening_own_link_gets_no_grant(client, world, fresh):
    r = client.post(REDEEM, headers=world["a_h"], json={"token": fresh["link"]["token"]})
    assert r.status_code == 200 and r.json()["data"] == {"is_owner": True}
    assert client.get(MINE, headers=world["a_h"]).json()["data"] == []
    # link is still unclaimed and usable by a real partner
    assert client.post(REDEEM, headers=fresh["p1"], json={"token": fresh["link"]["token"]}).status_code == 200


def test_non_customer_account_cannot_redeem(client, world, fresh):
    assert client.post(REDEEM, headers=world["admin"], json={"token": fresh["link"]["token"]}).status_code == 403
    assert client.get(MINE, headers=world["admin"]).status_code == 403


def test_bad_redeem_tokens(client, fresh):
    for tok in ("short", "A" * 43, "A" * 100, "", None):
        assert client.post(REDEEM, headers=fresh["p1"], json={"token": tok}).status_code == 404, tok
    assert client.post(REDEEM, headers=fresh["p1"], json={"token": {"$ne": ""}}).status_code == 404  # operator injection
    assert client.get(f"{MINE}/PAC-NOTREAL", headers=fresh["p1"]).status_code == 404
    assert client.get(f"{MINE}/PAC-AAAAAAAAAA", headers=fresh["p1"]).status_code == 404


def test_expired_and_revoked_links_cannot_be_redeemed(client, world, fresh):
    LOOP.run_until_complete(server.db["erp_booking_partner_shares"].update_one(
        {"token": fresh["link"]["token"]}, {"$set": {"expires_at": "2000-01-01T00:00:00+00:00"}}))
    assert client.post(REDEEM, headers=fresh["p1"], json={"token": fresh["link"]["token"]}).status_code == 410
    new = client.post(_url(fresh["bid"]), headers=world["a_h"], json={}).json()["data"]
    assert client.delete(_url(fresh["bid"]), headers=world["a_h"]).status_code == 200
    assert client.post(REDEEM, headers=fresh["p1"], json={"token": new["token"]}).status_code == 410


def test_owner_can_remove_one_partner_without_affecting_another(client, world, fresh):
    bid = fresh["bid"]
    d1 = client.post(REDEEM, headers=fresh["p1"], json={"token": fresh["link"]["token"]}).json()["data"]
    link2 = client.post(_url(bid), headers=world["a_h"], json={}).json()["data"]
    d2 = client.post(REDEEM, headers=fresh["p2"], json={"token": link2["token"]}).json()["data"]
    # other customers/partners cannot remove access
    assert client.delete(f"/api/bookings/{bid}/partner-access/{d1['access_id']}", headers=world["b_h"]).status_code == 404
    assert client.delete(f"/api/bookings/{bid}/partner-access/{d1['access_id']}", headers=fresh["p2"]).status_code == 404
    assert client.delete(f"/api/bookings/{bid}/partner-access/{d1['access_id']}", headers=world["a_h"]).status_code == 200
    assert client.get(f"{MINE}/{d1['access_id']}", headers=fresh["p1"]).status_code == 404
    assert client.get(MINE, headers=fresh["p1"]).json()["data"] == []
    assert client.get(f"{MINE}/{d2['access_id']}", headers=fresh["p2"]).status_code == 200
    # partner 1 cannot slip back in with the consumed link
    assert client.post(REDEEM, headers=fresh["p1"], json={"token": fresh["link"]["token"]}).status_code == 403


def test_stop_sharing_revokes_every_partner(client, world, fresh):
    bid = fresh["bid"]
    d1 = client.post(REDEEM, headers=fresh["p1"], json={"token": fresh["link"]["token"]}).json()["data"]
    assert client.delete(_url(bid), headers=world["a_h"]).status_code == 200
    assert client.get(f"{MINE}/{d1['access_id']}", headers=fresh["p1"]).status_code == 404
    assert client.get(f"/api/bookings/{bid}/partner-access", headers=world["a_h"]).json()["data"] == []
    # a fresh link cannot resurrect a withdrawn partner's old grant
    link = client.post(_url(bid), headers=world["a_h"], json={}).json()["data"]
    assert client.post(REDEEM, headers=fresh["p1"], json={"token": link["token"]}).status_code == 403


def test_other_customer_cannot_list_or_manage_partner_access(client, world, fresh):
    bid = fresh["bid"]
    assert client.get(f"/api/bookings/{bid}/partner-access", headers=world["b_h"]).status_code == 404
    assert client.get(f"/api/bookings/{bid}/partner-access", headers=fresh["p1"]).status_code == 404
    assert client.get(f"/api/bookings/{bid}/partner-access").status_code in (401, 403)


def test_redeem_is_rate_limited(client, fresh):
    codes = [client.post(REDEEM, headers=fresh["p2"], json={"token": "A" * 43}).status_code for _ in range(25)]
    assert codes[0] == 404 and 429 in codes
    partner_share._redeem_hits.clear()
