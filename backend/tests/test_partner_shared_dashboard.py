"""Partner Shared Booking Dashboard + security: Partner 1 (authorized) vs Partner 2 (not authorized).

Covers shared updates/documents, owner permissions, revoke / regenerate, link + access expiry, token validation,
and proves a partner can never reach the owner's bookings, invoices, documents or payment history through any API.
Runs the real FastAPI app in-process on mongomock-motor.
Run:  cd backend && python -m pytest -c /dev/null --rootdir=. tests/test_partner_shared_dashboard.py
"""
import asyncio
import base64
import os
import sys
import uuid
from pathlib import Path

import pytest

pytest.importorskip("mongomock_motor")
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "ntaxco_partner_dash_test")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123")
os.environ.pop("NTAXCO_ENABLE_DEMO_SEED", None)

import motor.motor_asyncio as _motor  # noqa: E402
_motor.AsyncIOMotorClient = lambda *a, **k: AsyncMongoMockClient()

from fastapi.testclient import TestClient  # noqa: E402
import server  # noqa: E402
import partner_share  # noqa: E402

LOOP = asyncio.new_event_loop()
REDEEM = "/api/partner/shares/redeem"
MINE = "/api/partner/shared-bookings"
PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PDF_URI = "data:application/pdf;base64," + base64.b64encode(PDF_BYTES).decode()


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


def _register(client, admin, name):
    mobile = "9" + str(uuid.uuid4().int)[:9]
    email = f"{name.lower()}.{uuid.uuid4().hex[:5]}@example.com"
    r = client.post("/api/auth/register", json={"full_name": name, "email": email, "password": "CustPass#123",
                                                 "role": "customer", "mobile": mobile})
    assert r.status_code == 200, r.text
    h = {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
    cid = [c for c in client.get("/api/customers", headers=admin).json()["data"] if c.get("email") == email][0]["id"]
    return h, cid, email


@pytest.fixture(scope="module")
def world(client, admin):
    svc = client.post("/api/services", headers=admin, json={
        "name": "GST Filing", "title": "GST Filing", "category": "GST", "description": "x", "final_price": 1000,
        "status": "Active"}).json()["data"]
    owner_h, owner_cid, owner_mail = _register(client, admin, "Owner")
    other_h, other_cid, other_mail = _register(client, admin, "Stranger")
    bk = client.post("/api/bookings", headers=owner_h, json={
        "service_id": svc["id"], "service": svc["name"], "email": owner_mail, "client_request_id": uuid.uuid4().hex})
    assert bk.status_code == 200, bk.text
    booking = bk.json()["data"]
    inv = client.post("/api/invoices", headers=admin, json={
        "invoice_no": "INV-PARTNER-1", "customer_id": owner_cid, "booking_id": booking["id"], "service_id": svc["id"],
        "taxable": 1000, "rate": 18}).json()["data"]
    pay = client.post("/api/payments", headers=admin, json={
        "customer_id": owner_cid, "invoice_no": "INV-PARTNER-1", "amount": 500, "status": "Completed",
        "payment_method": "UPI", "reference_no": "UTR-PARTNER-1", "payment_date": "2026-10-05"}).json()["data"]
    docs = {}
    for key in ("shared", "private"):
        r = client.post("/api/documents", headers=admin, json={
            "customer_id": owner_cid, "booking_id": booking["id"], "title": f"{key} doc", "name": f"{key} doc",
            "file_name": f"{key}.pdf", "file_data": PDF_URI})
        assert r.status_code == 200, r.text
        docs[key] = r.json()["data"]
    # a document that belongs to a different customer
    r = client.post("/api/documents", headers=admin, json={
        "customer_id": other_cid, "title": "stranger doc", "name": "stranger doc", "file_name": "s.pdf", "file_data": PDF_URI})
    assert r.status_code == 200, r.text
    docs["stranger"] = r.json()["data"]
    # progress the booking so the partner has an update to read
    assert client.put(f"/api/bookings/{booking['id']}", headers=admin, json={"status": "Running"}).status_code == 200
    return dict(admin=admin, owner=owner_h, other=other_h, booking=booking, invoice=inv, payment=pay, docs=docs,
                owner_cid=owner_cid)


@pytest.fixture()
def shared(client, world):
    """Fresh state: Partner 1 holds a grant (updates + 'shared' doc only); Partner 2 holds nothing."""
    partner_share._redeem_hits.clear()
    partner_share._hits.clear()
    bid = world["booking"]["id"]
    client.delete(f"/api/bookings/{bid}/partner-share", headers=world["owner"])
    link = client.post(f"/api/bookings/{bid}/partner-share", headers=world["owner"],
                       json={"document_ids": [world["docs"]["shared"]["id"]]})
    assert link.status_code == 200, link.text
    link = link.json()["data"]
    p1, _, p1_mail = _register(client, world["admin"], "Partner1")
    p2, _, p2_mail = _register(client, world["admin"], "Partner2")
    d1 = client.post(REDEEM, headers=p1, json={"token": link["token"]})
    assert d1.status_code == 200, d1.text
    return dict(bid=bid, link=link, p1=p1, p2=p2, p1_mail=p1_mail, p2_mail=p2_mail, d1=d1.json()["data"])


def _bk(bid):
    return f"/api/bookings/{bid}"


# ------------------------------------------------------------------ Partner 1 sees authorized data
def test_partner1_sees_authorized_booking_updates_and_documents(client, world, shared):
    aid = shared["d1"]["access_id"]
    rows = client.get(MINE, headers=shared["p1"]).json()["data"]
    assert [r["access_id"] for r in rows] == [aid]
    assert rows[0]["service"] == "GST Filing" and rows[0]["status"] == "Running" and rows[0]["documents_count"] == 1
    d = client.get(f"{MINE}/{aid}", headers=shared["p1"])
    assert d.status_code == 200, d.text
    assert d.headers.get("cache-control") == "no-store"
    d = d.json()["data"]
    assert d["service"] == "GST Filing" and d["status"] == "Running" and d["booking_date"]
    assert any("Running" in u["title"] for u in d["updates"]) and d["updates"][0]["at"]
    assert [x["doc_id"] for x in d["documents"]] == [world["docs"]["shared"]["id"]]
    assert "file_data" not in d["documents"][0]
    # no money, contact details, internal ids, invoices or payments in the partner payload
    flat = repr(d).lower()
    for banned in ("inv-partner-1", "utr-partner-1", "payment", "estimated_fee", "price", "customer_id", "mobile"):
        assert banned not in flat, banned
    assert not ({"id", "booking_id", "customer_id", "invoice", "invoices", "payments"} & set(d))


def test_partner1_downloads_only_the_document_the_owner_shared(client, world, shared):
    aid, docs = shared["d1"]["access_id"], world["docs"]
    r = client.get(f"{MINE}/{aid}/documents/{docs['shared']['id']}", headers=shared["p1"])
    assert r.status_code == 200, r.text
    got = r.json()["data"]
    assert got["file_name"] == "shared.pdf" and got["file_data"] == PDF_URI
    # documents the owner did NOT tick, or that belong to someone else, are a plain 404
    for key in ("private", "stranger"):
        assert client.get(f"{MINE}/{aid}/documents/{docs[key]['id']}", headers=shared["p1"]).status_code == 404, key
    assert client.get(f"{MINE}/{aid}/documents/..%2F..%2Fx", headers=shared["p1"]).status_code in (404, 422)


# ------------------------------------------------------------------ Partner 2 sees nothing
def test_partner2_cannot_reach_partner1_data(client, world, shared):
    aid, doc = shared["d1"]["access_id"], world["docs"]["shared"]["id"]
    assert client.get(MINE, headers=shared["p2"]).json()["data"] == []
    assert client.get(f"{MINE}/{aid}", headers=shared["p2"]).status_code == 404
    assert client.get(f"{MINE}/{aid}/documents/{doc}", headers=shared["p2"]).status_code == 404
    # unauthenticated
    assert client.get(f"{MINE}/{aid}").status_code in (401, 403)
    assert client.get(f"{MINE}/{aid}/documents/{doc}").status_code in (401, 403)
    # Partner 1's already-claimed link is refused for Partner 2
    assert client.post(REDEEM, headers=shared["p2"], json={"token": shared["link"]["token"]}).status_code == 403
    assert client.get(MINE, headers=shared["p2"]).json()["data"] == []


def test_partner2_cannot_use_owner_or_partner_management_endpoints(client, world, shared):
    bid, aid = shared["bid"], shared["d1"]["access_id"]
    for h in (shared["p2"], shared["p1"], world["other"]):
        assert client.get(f"{_bk(bid)}/partner-share", headers=h).status_code == 404
        assert client.get(f"{_bk(bid)}/partner-access", headers=h).status_code == 404
        assert client.get(f"{_bk(bid)}/partner-share/documents", headers=h).status_code == 404
        assert client.patch(f"{_bk(bid)}/partner-access/{aid}", headers=h, json={"show_updates": False}).status_code == 404
        assert client.delete(f"{_bk(bid)}/partner-access/{aid}", headers=h).status_code == 404
        assert client.post(f"{_bk(bid)}/partner-share", headers=h, json={"regenerate": True}).status_code == 404
        assert client.delete(f"{_bk(bid)}/partner-share", headers=h).status_code == 404
    # Partner 1 is still authorized afterwards (none of the attempts changed anything)
    assert client.get(f"{MINE}/{aid}", headers=shared["p1"]).status_code == 200


# ------------------------------------------- invoices / payment history / documents / bookings stay private
def test_partner_cannot_read_owner_bookings_invoices_payments_or_documents(client, world, shared):
    for label, h in (("partner1", shared["p1"]), ("partner2", shared["p2"]), ("stranger", world["other"])):
        assert client.get(_bk(shared["bid"]), headers=h).status_code == 404, label
        assert client.get(f"/api/invoices/{world['invoice']['id']}", headers=h).status_code == 404, label
        assert client.get(f"/api/payments/{world['payment']['id']}", headers=h).status_code == 404, label
        for key in ("shared", "private"):  # even the document the owner shared is NOT readable via the owner API
            assert client.get(f"/api/documents/{world['docs'][key]['id']}", headers=h).status_code == 404, (label, key)
        for path in ("/api/bookings", "/api/invoices", "/api/payments", "/api/documents"):
            rows = client.get(path, headers=h).json()["data"]
            blob = repr(rows)
            assert world["booking"]["id"] not in blob and "INV-PARTNER-1" not in blob and "UTR-PARTNER-1" not in blob, (label, path)
        assert client.put(_bk(shared["bid"]), headers=h, json={"status": "Cancelled"}).status_code in (403, 404), label
        assert client.delete(f"/api/documents/{world['docs']['shared']['id']}", headers=h).status_code in (403, 404), label
    # sanity: the owner still has all of it
    assert client.get(f"/api/invoices/{world['invoice']['id']}", headers=world["owner"]).status_code == 200
    assert client.get(f"/api/payments/{world['payment']['id']}", headers=world["owner"]).status_code == 200
    assert client.get(f"/api/documents/{world['docs']['private']['id']}", headers=world["owner"]).status_code == 200


def test_partner_cannot_read_booking_chat(client, world, shared):
    assert client.get(f"{_bk(shared['bid'])}/messages", headers=shared["p1"]).status_code in (403, 404)


# ------------------------------------------------------------------ owner permissions
def test_owner_cannot_share_someone_elses_document(client, world, shared):
    bid = shared["bid"]
    r = client.post(f"{_bk(bid)}/partner-share", headers=world["owner"],
                    json={"regenerate": True, "document_ids": [world["docs"]["stranger"]["id"]]})
    assert r.status_code == 404
    aid = shared["d1"]["access_id"]
    assert client.patch(f"{_bk(bid)}/partner-access/{aid}", headers=world["owner"],
                        json={"document_ids": [world["docs"]["stranger"]["id"]]}).status_code == 404
    assert client.patch(f"{_bk(bid)}/partner-access/{aid}", headers=world["owner"],
                        json={"document_ids": [{"$ne": ""}]}).status_code == 422
    assert client.patch(f"{_bk(bid)}/partner-access/{aid}", headers=world["owner"],
                        json={"document_ids": "x" * 5}).status_code == 422


def test_owner_lists_only_own_shareable_documents(client, world, shared):
    rows = client.get(f"{_bk(shared['bid'])}/partner-share/documents", headers=world["owner"]).json()["data"]
    ids = {r["doc_id"] for r in rows}
    assert {world["docs"]["shared"]["id"], world["docs"]["private"]["id"]} <= ids
    assert world["docs"]["stranger"]["id"] not in ids
    assert all("file_data" not in r for r in rows)


def test_owner_changes_what_partner_sees_immediately(client, world, shared):
    bid, aid = shared["bid"], shared["d1"]["access_id"]
    private = world["docs"]["private"]["id"]
    assert client.get(f"{MINE}/{aid}/documents/{private}", headers=shared["p1"]).status_code == 404
    r = client.patch(f"{_bk(bid)}/partner-access/{aid}", headers=world["owner"],
                     json={"document_ids": [private], "show_updates": False})
    assert r.status_code == 200, r.text
    assert client.get(f"{MINE}/{aid}/documents/{private}", headers=shared["p1"]).status_code == 200
    assert client.get(f"{MINE}/{aid}/documents/{world['docs']['shared']['id']}", headers=shared["p1"]).status_code == 404
    d = client.get(f"{MINE}/{aid}", headers=shared["p1"]).json()["data"]
    assert d["updates"] == [] and [x["doc_id"] for x in d["documents"]] == [private]
    assert d["status"] == "Running"  # status is always visible; the timeline is optional


# ------------------------------------------------------------------ revoke / regenerate
def test_owner_revokes_partner1_and_access_ends_everywhere(client, world, shared):
    bid, aid, doc = shared["bid"], shared["d1"]["access_id"], world["docs"]["shared"]["id"]
    assert client.delete(f"{_bk(bid)}/partner-access/{aid}", headers=world["owner"]).status_code == 200
    assert client.get(MINE, headers=shared["p1"]).json()["data"] == []
    assert client.get(f"{MINE}/{aid}", headers=shared["p1"]).status_code == 404
    assert client.get(f"{MINE}/{aid}/documents/{doc}", headers=shared["p1"]).status_code == 404
    # the old link cannot bring them back
    assert client.post(REDEEM, headers=shared["p1"], json={"token": shared["link"]["token"]}).status_code == 403


def test_regenerate_kills_old_link_and_keeps_sharing_choices(client, world, admin):
    partner_share._redeem_hits.clear()
    bid = world["booking"]["id"]
    client.delete(f"{_bk(bid)}/partner-share", headers=world["owner"])
    first = client.post(f"{_bk(bid)}/partner-share", headers=world["owner"],
                        json={"document_ids": [world["docs"]["shared"]["id"]], "show_updates": False, "expires_in_days": 7}).json()["data"]
    new = client.post(f"{_bk(bid)}/partner-share", headers=world["owner"], json={"regenerate": True}).json()["data"]
    assert new["token"] != first["token"]
    assert new["document_ids"] == [world["docs"]["shared"]["id"]] and new["show_updates"] is False
    assert client.get(f"/api/public/partner-share/{first['token']}").status_code == 410
    p3, _, _ = _register(client, admin, "Partner3")
    assert client.post(REDEEM, headers=p3, json={"token": first["token"]}).status_code == 410
    assert client.post(REDEEM, headers=p3, json={"token": new["token"]}).status_code == 200


def test_dashboard_lists_my_shares_with_link_and_partners(client, world, shared):
    rows = client.get("/api/partner/my-shares", headers=world["owner"]).json()["data"]
    mine = [r for r in rows if r["booking_id"] == shared["bid"]]
    assert len(mine) == 1 and mine[0]["service"] == "GST Filing" and mine[0]["status"] == "Running"
    assert [p["access_id"] for p in mine[0]["partners"]] == [shared["d1"]["access_id"]]
    # nobody else sees it
    for h in (shared["p1"], shared["p2"], world["other"]):
        assert shared["bid"] not in repr(client.get("/api/partner/my-shares", headers=h).json()["data"])
    assert client.get("/api/partner/my-shares").status_code in (401, 403)
    assert client.get("/api/partner/my-shares", headers=world["admin"]).status_code == 403


# ------------------------------------------------------------------ expiry + token validation
def test_link_expiry_is_chosen_by_owner_and_bounded(client, world, shared):
    bid = shared["bid"]
    client.delete(f"{_bk(bid)}/partner-share", headers=world["owner"])
    for bad in (0, -1, 91, 3650, "abc", True, [7], {"a": 1}):
        r = client.post(f"{_bk(bid)}/partner-share", headers=world["owner"], json={"expires_in_days": bad})
        assert r.status_code == 422, (bad, r.text)
    link = client.post(f"{_bk(bid)}/partner-share", headers=world["owner"], json={"expires_in_days": 2}).json()["data"]
    from datetime import datetime, timezone
    left = datetime.fromisoformat(link["expires_at"]) - datetime.now(timezone.utc)
    assert 1.9 < left.total_seconds() / 86400 <= 2.0


def test_access_expiry_ends_partner_access_even_without_revoke(client, world, admin):
    partner_share._redeem_hits.clear()
    bid = world["booking"]["id"]
    client.delete(f"{_bk(bid)}/partner-share", headers=world["owner"])
    link = client.post(f"{_bk(bid)}/partner-share", headers=world["owner"], json={"access_days": 5,
                       "document_ids": [world["docs"]["shared"]["id"]]}).json()["data"]
    p, _, _ = _register(client, admin, "Partner4")
    d = client.post(REDEEM, headers=p, json={"token": link["token"]}).json()["data"]
    assert d["access_expires_at"]
    assert client.get(f"{MINE}/{d['access_id']}", headers=p).status_code == 200
    LOOP.run_until_complete(server.db["erp_booking_partner_access"].update_one(
        {"id": d["access_id"]}, {"$set": {"access_expires_at": "2000-01-01T00:00:00+00:00"}}))
    assert client.get(f"{MINE}/{d['access_id']}", headers=p).status_code == 404
    assert client.get(MINE, headers=p).json()["data"] == []
    assert client.get(f"{MINE}/{d['access_id']}/documents/{world['docs']['shared']['id']}", headers=p).status_code == 404
    # an expired grant cannot be revived by re-presenting the same (consumed) link
    assert client.post(REDEEM, headers=p, json={"token": link["token"]}).status_code == 403


def test_forged_and_malformed_identifiers_are_rejected(client, shared):
    h, aid = shared["p1"], shared["d1"]["access_id"]
    for bad in ("PAC-", "pac-aaaaaaaaaa", "PAC-AAAAAAAAAAA", "PAC-$ne", "1", "PAC-AAAAAAAAAA"):
        assert client.get(f"{MINE}/{bad}", headers=h).status_code == 404, bad
    assert client.get(f"{MINE}/{aid}/documents/DOC-NOPE", headers=h).status_code == 404
    for tok in ("A" * 31, "A" * 65, "../etc/passwd", "A" * 40 + "%24ne", "A" * 43):
        assert client.get(f"/api/public/partner-share/{tok}").status_code == 404, tok
        assert client.post(REDEEM, headers=h, json={"token": tok}).status_code == 404, tok

# ------------------------------------------------------------------ recursive partner delegation / isolation

def test_partner2_can_delegate_only_within_original_customer_permissions(client, world, shared, admin):
    # Original customer authorized updates + one document for Partner 1.
    aid1 = shared["d1"]["access_id"]
    p2, _, p2_mail = _register(client, admin, "SubPartner2")
    create = client.post(f"{MINE}/{aid1}/share", headers=shared["p1"], json={
        "partner_email": p2_mail, "show_updates": True,
        "document_ids": [world["docs"]["shared"]["id"]],
    })
    assert create.status_code == 200, create.text
    child_link = create.json()["data"]
    assert child_link["booking_id"] == world["booking"]["id"]
    assert client.post(REDEEM, headers=p2, json={"token": child_link["token"]}).status_code == 200
    child_list = client.get(MINE, headers=p2).json()["data"]
    assert len(child_list) == 1
    aid2 = child_list[0]["access_id"]
    assert client.get(f"{MINE}/{aid2}", headers=p2).status_code == 200
    # Parent cannot see the child's account-level grants as its own bookings; it sees only its own grant.
    assert [x["access_id"] for x in client.get(MINE, headers=shared["p1"]).json()["data"]] == [aid1]
    # Original customer permissions are an upper bound; no document or update permission can be escalated.
    denied = client.post(f"{MINE}/{aid1}/share", headers=shared["p1"], json={
        "document_ids": [world["docs"]["private"]["id"]],
    })
    assert denied.status_code == 403


def test_recursive_delegation_isolated_and_parent_revocation_invalidates_descendants(client, world, shared, admin):
    aid1 = shared["d1"]["access_id"]
    p2, _, p2_mail = _register(client, admin, "HierarchyP2")
    p3, _, p3_mail = _register(client, admin, "HierarchyP3")
    link2 = client.post(f"{MINE}/{aid1}/share", headers=shared["p1"], json={"partner_email": p2_mail}).json()["data"]
    assert client.post(REDEEM, headers=p2, json={"token": link2["token"]}).status_code == 200
    aid2 = client.get(MINE, headers=p2).json()["data"][0]["access_id"]
    link3_response = client.post(f"{MINE}/{aid2}/share", headers=p2, json={"partner_email": p3_mail})
    assert link3_response.status_code == 200, link3_response.text
    link3 = link3_response.json()["data"]
    assert client.post(REDEEM, headers=p3, json={"token": link3["token"]}).status_code == 200
    aid3 = client.get(MINE, headers=p3).json()["data"][0]["access_id"]
    # Partner 1 cannot see Partner 2's or Partner 3's grant IDs or their account data.
    assert client.get(f"{MINE}/{aid2}", headers=shared["p1"]).status_code == 404
    assert client.get(f"{MINE}/{aid3}", headers=shared["p1"]).status_code == 404
    assert len(client.get(MINE, headers=shared["p1"]).json()["data"]) == 1
    # Removing the ancestor grant invalidates all descendants even if child records still exist.
    assert client.delete(f"/api/bookings/{shared['bid']}/partner-access/{aid1}", headers=world["owner"]).status_code == 200
    assert client.get(MINE, headers=p2).json()["data"] == []
    assert client.get(MINE, headers=p3).json()["data"] == []
    assert client.get(f"{MINE}/{aid2}", headers=p2).status_code == 404
    assert client.get(f"{MINE}/{aid3}", headers=p3).status_code == 404
    assert client.get(f"/api/public/partner-share/{link3['token']}").status_code == 410


def test_delegated_link_expiry_and_revocation_are_enforced(client, world, shared, admin):
    aid1 = shared["d1"]["access_id"]
    p2, _, p2_mail = _register(client, admin, "ExpirySubPartner")
    link = client.post(f"{MINE}/{aid1}/share", headers=shared["p1"], json={"partner_email": p2_mail}).json()["data"]
    # Expiring the ancestor invalidates both its access and all descendant invite links.
    LOOP.run_until_complete(server.db["erp_booking_partner_access"].update_one(
        {"id": aid1}, {"$set": {"access_expires_at": "2000-01-01T00:00:00+00:00"}}))
    assert client.get(f"/api/public/partner-share/{link['token']}").status_code == 410
    assert client.post(REDEEM, headers=p2, json={"token": link["token"]}).status_code == 410
