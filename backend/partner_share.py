"""Share a booking with a partner: invite link + partner login + per-partner access grants.

Collections
  erp_booking_partner_shares   the invite link (one per partner invite)
      id PSH-XXXXXXXXXX, booking_id, booking_no, customer_id, token (256-bit), link_path, share_url,
      partner_email (optional - restricts the link to that email), created_at/created_by/expires_at,
      revoked/revoked_at, claimed_by/claimed_by_email/claimed_at (set once, by the first partner to redeem),
      view_count/last_viewed_at
  erp_booking_partner_access   one row per (booking, partner account) - the ONLY thing that grants a partner read access
      id PAC-XXXXXXXXXX, booking_id, share_id, owner_customer_id, partner_user_id, partner_email, partner_name,
      granted_at, revoked/revoked_at, last_viewed_at

Flow
  1. Owner generates a link (optionally for one specific partner email).
  2. Partner opens /partner/booking/<token>; the frontend makes them log in / register with THEIR OWN account.
  3. The authenticated partner redeems the token. The backend verifies the link (valid, not expired/revoked,
     not the partner's own booking, email matches if restricted, not already claimed by another account) and
     writes an access grant for that account. A link serves exactly one partner: once claimed, other accounts
     get 403 and the owner must generate a separate link for them.
  4. The partner can only read bookings for which their own account holds an active grant.

Endpoints
  owner (customer who made the booking)
    POST   /api/bookings/{booking_id}/partner-share                       create (idempotent) / regenerate invite link
                                                                          body: partner_email, regenerate, expires_in_days,
                                                                          access_days, show_updates, document_ids
    GET    /api/bookings/{booking_id}/partner-share                       current unclaimed invite link or null
    DELETE /api/bookings/{booking_id}/partner-share                       stop sharing: revoke all links + all partner access
    GET    /api/bookings/{booking_id}/partner-access                      partners who currently have access
    DELETE /api/bookings/{booking_id}/partner-access/{access_id}          remove one partner's access
    PATCH  /api/bookings/{booking_id}/partner-access/{access_id}          change what that partner may see (updates / documents)
    GET    /api/bookings/{booking_id}/partner-share/documents             my documents I may choose to share for this booking
    GET    /api/partner/my-shares                                         dashboard: every booking I shared, its link + partners
  public
    GET    /api/public/partner-share/{token}                              link status only (valid / restricted) - NO booking data
  partner (any authenticated customer-site account)
    POST   /api/partner/shares/redeem                                     body {token}; verify link, grant access
    GET    /api/partner/shared-bookings                                   bookings shared with ME
    GET    /api/partner/shared-bookings/{access_id}                       one of them (404 unless my grant)
    GET    /api/partner/shared-bookings/{access_id}/documents/{doc_id}    download ONE document the owner explicitly shared

Partners never receive internal booking ids, payment/amount, contact details, invoices, payment history or chat.
Documents are visible only when the owner explicitly ticked them for that partner; status updates can be switched off.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from index_utils import ensure_index

logger = logging.getLogger("ntaxco.partner_share")

COLLECTION = "erp_booking_partner_shares"
ACCESS_COLLECTION = "erp_booking_partner_access"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
ACCESS_ID_RE = re.compile(r"^PAC-[A-Z0-9]{10}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
LINK_PATH_PREFIX = "/partner/booking/"
DOC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEFAULT_TTL_DAYS = 30
MAX_LINK_DAYS = 90              # an owner can pick a shorter link life; never longer than this
MAX_ACCESS_DAYS = 365           # optional automatic end of a partner's access after they join
MAX_SHARED_DOCUMENTS = 25
PUBLIC_RATE_LIMIT = 60          # requests ...
PUBLIC_RATE_WINDOW = 60         # ... per this many seconds, per client IP
REDEEM_RATE_LIMIT = 20          # redeem attempts per window per client IP

_hits: dict[str, deque] = defaultdict(deque)
_redeem_hits: dict[str, deque] = defaultdict(deque)


def _ttl_days() -> int:
    try:
        days = int(os.getenv("PARTNER_SHARE_TTL_DAYS", DEFAULT_TTL_DAYS))
    except ValueError:
        days = DEFAULT_TTL_DAYS
    return min(max(days, 1), 365)


def _base_url() -> str:
    return (os.getenv("PARTNER_SHARE_BASE_URL") or "").strip().rstrip("/")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff and xff.split(",")[0].strip():
        return xff.split(",")[0].strip()
    return request.client.host if request.client and request.client.host else "unknown"


def _sliding_window(store: dict, request: Request, limit: int) -> None:
    key = _client_ip(request)
    now = time.monotonic()
    window = store[key]
    while window and now - window[0] > PUBLIC_RATE_WINDOW:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(status_code=429, detail="Too many requests. Please try again in a minute.")
    window.append(now)
    if len(store) > 5000:  # bound memory
        for k in [k for k, v in store.items() if not v]:
            store.pop(k, None)


def _rate_limit(request: Request) -> None:
    """Small in-process sliding window so the public endpoint cannot be hammered."""
    _sliding_window(_hits, request, PUBLIC_RATE_LIMIT)


def _redeem_rate_limit(request: Request) -> None:
    """Tighter limit for token redemption so tokens cannot be brute-forced through the authenticated endpoint."""
    _sliding_window(_redeem_hits, request, REDEEM_RATE_LIMIT)


def _norm_email(value) -> str | None:
    v = str(value or "").strip().lower()
    return v or None


def _present_owner_share(share: dict) -> dict:
    """Shape returned to the booking owner (includes the token so the link can be copied again)."""
    return {
        "id": share["id"],
        "booking_id": share["booking_id"],
        "booking_no": share.get("booking_no"),
        "token": share["token"],
        "link_path": share["link_path"],
        "share_url": share.get("share_url"),
        "partner_email": share.get("partner_email"),
        "created_at": share.get("created_at"),
        "expires_at": share.get("expires_at"),
        "view_count": share.get("view_count", 0),
        "last_viewed_at": share.get("last_viewed_at"),
        "show_updates": share.get("show_updates", True),
        "document_ids": list(share.get("document_ids") or []),
        "access_days": share.get("access_days"),
    }


def _present_owner_access(grant: dict) -> dict:
    return {
        "access_id": grant["id"],
        "partner_name": grant.get("partner_name"),
        "partner_email": grant.get("partner_email"),
        "registration_status": "Registered",
        "invitation_claimed_at": grant.get("invitation_claimed_at"),
        "account_created_at": grant.get("account_created_at"),
        "granted_at": grant.get("granted_at"),
        "last_viewed_at": grant.get("last_viewed_at"),
        "access_expires_at": grant.get("access_expires_at"),
        "access_status": ("Revoked" if grant.get("revoked") else "Inactive" if grant.get("active") is False else "Expired" if (_parse_dt(grant.get("access_expires_at")) and _parse_dt(grant.get("access_expires_at")) <= _now()) else "Active"),
        "active": grant.get("active", True),
        "revoked": bool(grant.get("revoked")),
        "show_updates": grant.get("show_updates", True),
        "document_ids": list(grant.get("document_ids") or []),
    }


def _booking_date(booking: dict):
    return booking.get("booking_date") or (str(booking["created_at"])[:10] if booking.get("created_at") else None)


def _bounded_days(value, default, maximum, field):
    """Owner-supplied day counts: integers 1..maximum, or the default when omitted."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise HTTPException(status_code=422, detail=f"{field} must be a whole number of days.")
    try:
        days = int(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} must be a whole number of days.")
    if days < 1 or days > maximum:
        raise HTTPException(status_code=422, detail=f"{field} must be between 1 and {maximum} days.")
    return days


def _clean_doc_ids(value) -> list:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > MAX_SHARED_DOCUMENTS:
        raise HTTPException(status_code=422, detail=f"Choose up to {MAX_SHARED_DOCUMENTS} documents to share.")
    out = []
    for item in value:
        if not isinstance(item, str) or not DOC_ID_RE.match(item):
            raise HTTPException(status_code=422, detail="One of the selected documents is not valid.")
        if item not in out:
            out.append(item)
    return out


async def ensure_partner_share_indexes(db) -> None:
    coll = db[COLLECTION]
    await ensure_index(coll, [("token", 1)], unique=True, name="uniq_partner_share_token")
    await ensure_index(coll, [("booking_id", 1), ("revoked", 1)], name="partner_share_booking")
    await ensure_index(coll, [("customer_id", 1)], name="partner_share_customer")
    access = db[ACCESS_COLLECTION]
    await ensure_index(access, [("id", 1)], unique=True, name="uniq_partner_access_id")
    # One grant per (booking, partner account): makes redeem idempotent and race-safe.
    await ensure_index(access, [("booking_id", 1), ("partner_user_id", 1)], unique=True, name="uniq_partner_access_booking_user")
    await ensure_index(access, [("partner_user_id", 1), ("revoked", 1)], name="partner_access_user")


def build_partner_share_router(db, get_current_user) -> APIRouter:
    router = APIRouter(prefix="/api")
    coll = db[COLLECTION]
    access_coll = db[ACCESS_COLLECTION]

    # ------------------------------------------------------------------ helpers
    def _require_customer_account(user: dict) -> None:
        if user.get("role") != "customer":
            raise HTTPException(status_code=403, detail="Please sign in with a customer account to view shared bookings.")

    async def _owner_ids(user: dict) -> set:
        """Ids that identify this logged-in customer (same ownership rule as the bookings CRUD)."""
        if user.get("role") != "customer":
            raise HTTPException(status_code=403, detail="Only the customer who made the booking can share it")
        meta_id = (user.get("meta") or {}).get("customer_id")
        if not meta_id:
            clauses = [c for c in ({"email": user.get("email")} if user.get("email") else None,
                                   {"mobile": user.get("mobile")} if user.get("mobile") else None) if c]
            if clauses:
                record = await db["erp_customers"].find_one({"$or": clauses}, {"_id": 0, "id": 1})
                if record:
                    meta_id = record["id"]
        return {i for i in (meta_id, user.get("id")) if i}

    async def _owned_booking(booking_id: str, user: dict) -> dict:
        owners = await _owner_ids(user)
        booking = await db["erp_bookings"].find_one({"id": booking_id}, {"_id": 0})
        # 404 (not 403) for someone else's booking so ids cannot be probed.
        if not booking or not owners or booking.get("customer_id") not in owners:
            raise HTTPException(status_code=404, detail="Booking not found")
        return booking

    async def _shareable_documents(booking: dict, user: dict) -> list:
        """The owner's own documents that may be offered to a partner for this booking (never file bytes)."""
        owners = list(await _owner_ids(user))
        if not owners:
            return []
        query = {"customer_id": {"$in": owners}, "$or": [{"booking_id": booking["id"]}, {"booking_id": None},
                                                         {"booking_id": {"$exists": False}}, {"booking_id": ""}]}
        rows = await db["erp_documents"].find(query, {"_id": 0, "file_data": 0}).sort("uploaded_date", -1).to_list(200)
        return rows

    async def _validated_document_ids(booking: dict, user: dict, ids: list) -> list:
        """Only documents the owner really owns (and that belong to this booking or to no booking) can be shared."""
        if not ids:
            return []
        allowed = {d.get("id") for d in await _shareable_documents(booking, user)}
        bad = [i for i in ids if i not in allowed]
        if bad:
            # Same answer for "does not exist" and "belongs to someone else": ids cannot be probed.
            raise HTTPException(status_code=404, detail="One of the selected documents was not found.")
        return ids

    def _is_active(share: dict | None) -> bool:
        if not share or share.get("revoked"):
            return False
        expires = _parse_dt(share.get("expires_at"))
        return not (expires and expires <= _now())

    def _grant_active(grant: dict | None) -> bool:
        if not grant or grant.get("revoked") or grant.get("active") is False:
            return False
        ends = _parse_dt(grant.get("access_expires_at"))
        return not (ends and ends <= _now())

    async def _grant_chain_active(grant: dict | None, seen: set | None = None) -> bool:
        """A delegated grant is valid only while every ancestor grant remains valid."""
        if not _grant_active(grant):
            return False
        parent_id = (grant or {}).get("parent_access_id")
        if not parent_id:
            return True
        seen = seen or set()
        if parent_id in seen:
            return False  # fail closed if corrupted/cyclic relationship data exists
        seen.add(parent_id)
        parent = await access_coll.find_one({"id": parent_id}, {"_id": 0})
        if not parent or parent.get("booking_id") != grant.get("booking_id"):
            return False
        return await _grant_chain_active(parent, seen)

    async def _parent_grant_for_user(access_id: str, user: dict) -> dict:
        grant = await _my_grant(access_id, user)
        if not await _grant_chain_active(grant):
            raise HTTPException(status_code=404, detail="Shared booking not found")
        return grant

    async def _effective_permissions(grant: dict) -> dict:
        """Intersect this grant with every ancestor so later owner restrictions propagate downward."""
        effective = dict(grant)
        allowed_docs = set(grant.get("document_ids") or [])
        can_updates = bool(grant.get("show_updates", True))
        parent_id = grant.get("parent_access_id")
        seen = set()
        while parent_id:
            if parent_id in seen:
                return {**effective, "document_ids": [], "show_updates": False}
            seen.add(parent_id)
            parent = await access_coll.find_one({"id": parent_id}, {"_id": 0})
            if not parent or not await _grant_active(parent):
                return {**effective, "document_ids": [], "show_updates": False}
            allowed_docs.intersection_update(parent.get("document_ids") or [])
            can_updates = can_updates and bool(parent.get("show_updates", True))
            parent_id = parent.get("parent_access_id")
        effective["document_ids"] = [doc for doc in (grant.get("document_ids") or []) if doc in allowed_docs]
        effective["show_updates"] = can_updates
        return effective

    async def _active_share(booking_id: str, partner_email: str | None = None):
        """Newest usable invite for this booking + restriction. Claimed links are consumed (they belong to one partner)."""
        query = {"booking_id": booking_id, "revoked": {"$ne": True}, "claimed_by": None}
        async for share in coll.find(query, {"_id": 0}).sort("created_at", -1):
            if _is_active(share) and _norm_email(share.get("partner_email")) == partner_email:
                return share
        return None

    async def _latest_unclaimed_share(booking_id: str):
        query = {"booking_id": booking_id, "revoked": {"$ne": True}, "claimed_by": None}
        async for share in coll.find(query, {"_id": 0}).sort("created_at", -1):
            if _is_active(share):
                return share
        return None

    async def _create_share(booking: dict, user: dict, partner_email: str | None, *, link_days: int | None = None,
                            access_days: int | None = None, show_updates: bool = True, document_ids: list | None = None) -> dict:
        now = _now()
        token = secrets.token_urlsafe(32)
        link_path = f"{LINK_PATH_PREFIX}{token}"
        base = _base_url()
        share = {
            "id": "PSH-" + uuid.uuid4().hex[:10].upper(),
            "booking_id": booking["id"],
            "booking_no": booking.get("booking_no") or booking["id"],
            "customer_id": booking.get("customer_id"),
            "token": token,
            "link_path": link_path,
            "share_url": f"{base}{link_path}" if base else None,
            "partner_email": partner_email,
            "created_at": now.isoformat(),
            "created_by": user.get("id"),
            "expires_at": (now + timedelta(days=link_days or _ttl_days())).isoformat(),
            "revoked": False,
            "claimed_by": None,
            "view_count": 0,
            # What the partner who redeems this link may see (copied onto their grant; owner can change it later).
            "show_updates": bool(show_updates),
            "document_ids": list(document_ids or []),
            "access_days": access_days,
        }
        await coll.insert_one({**share})
        return share

    async def _summary(booking: dict, grant: dict) -> dict:
        """What a partner may see. Deliberately excludes internal ids, money, contact details and documents."""
        grant = await _effective_permissions(grant)
        customer = await db["erp_customers"].find_one(
            {"id": grant.get("owner_customer_id")}, {"_id": 0, "customer_name": 1, "name": 1, "business_name": 1, "owner": 1}) or {}
        return {
            "access_id": grant["id"],
            "booking_no": booking.get("booking_no") or booking.get("id"),
            "service": booking.get("service") or booking.get("service_name"),
            "status": booking.get("status"),
            "booking_date": _booking_date(booking),
            "due_date": booking.get("due_date"),
            "estimated_processing": booking.get("processing_time_snapshot") or booking.get("processing_time"),
            "consultant": booking.get("assigned_agent") or booking.get("assigned_employee"),
            "shared_by": customer.get("customer_name") or customer.get("name") or customer.get("business_name") or customer.get("owner"),
            "granted_at": grant.get("granted_at"),
            "access_expires_at": grant.get("access_expires_at"),
            "can_view_updates": grant.get("show_updates", True),
            "documents_count": len(grant.get("document_ids") or []),
        }

    def _updates(booking: dict) -> list:
        """Customer-safe progress timeline derived from the booking itself (no notes, chat, money or staff-only fields)."""
        events = []
        if booking.get("created_at"):
            events.append({"title": "Booking created", "status": None, "at": str(booking["created_at"])})
        for h in booking.get("status_history") or []:
            if isinstance(h, dict) and h.get("status") and h.get("at"):
                events.append({"title": f"Status changed to {h['status']}", "status": h["status"], "at": str(h["at"])})
        if booking.get("status_updated_at") and booking.get("status") and not any(
                e["status"] == booking["status"] and e["at"] == str(booking["status_updated_at"]) for e in events):
            events.append({"title": f"Status changed to {booking['status']}", "status": booking["status"],
                           "at": str(booking["status_updated_at"])})
        events.sort(key=lambda e: e["at"], reverse=True)
        return events[:50]

    async def _permitted_documents(grant: dict) -> list:
        """Documents this partner may see: ticked by the owner, still owned by the owner, metadata only."""
        ids = list(grant.get("document_ids") or [])
        if not ids:
            return []
        owner = grant.get("owner_customer_id")
        rows = await db["erp_documents"].find({"id": {"$in": ids}, "customer_id": owner},
                                              {"_id": 0, "file_data": 0}).to_list(MAX_SHARED_DOCUMENTS)
        by_id = {r["id"]: r for r in rows}
        return [{"doc_id": i, "name": by_id[i].get("title") or by_id[i].get("name") or by_id[i].get("file_name") or "Document",
                 "file_name": by_id[i].get("file_name"), "type": by_id[i].get("mime_type") or by_id[i].get("type"),
                 "size": by_id[i].get("file_size") or by_id[i].get("size"), "uploaded_date": by_id[i].get("uploaded_date")}
                for i in ids if i in by_id]

    async def _detail(booking: dict, grant: dict) -> dict:
        grant = await _effective_permissions(grant)
        data = await _summary(booking, grant)
        data["updates"] = _updates(booking) if grant.get("show_updates", True) else []
        data["documents"] = await _permitted_documents(grant)
        return data

    async def _my_grant(access_id: str, user: dict) -> dict:
        """The single authorization check for partner reads: an active grant held by THIS account."""
        if not ACCESS_ID_RE.match(access_id or ""):
            raise HTTPException(status_code=404, detail="Shared booking not found")
        grant = await access_coll.find_one(
            {"id": access_id, "partner_user_id": user.get("id"), "revoked": {"$ne": True}}, {"_id": 0})
        if not await _grant_chain_active(grant):
            raise HTTPException(status_code=404, detail="Shared booking not found")
        return grant

    def _no_store(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"

    async def _revoke_grants(query: dict) -> int:
        res = await access_coll.update_many({**query, "revoked": {"$ne": True}},
                                            {"$set": {"revoked": True, "revoked_at": _now().isoformat()}})
        return res.modified_count

    # ------------------------------------------------------------ owner endpoints
    @router.post("/bookings/{booking_id}/partner-share")
    async def generate_partner_share(booking_id: str, body: dict = Body(default={}), user: dict = Depends(get_current_user)):
        booking = await _owned_booking(booking_id, user)
        if str(booking.get("status") or "").lower() == "cancelled":
            raise HTTPException(status_code=409, detail="A cancelled booking cannot be shared")
        body = body or {}
        regenerate = bool(body.get("regenerate"))
        partner_email = _norm_email(body.get("partner_email"))
        if partner_email and not EMAIL_RE.match(partner_email):
            raise HTTPException(status_code=422, detail="Enter a valid partner email address.")
        link_days = _bounded_days(body.get("expires_in_days"), None, MAX_LINK_DAYS, "Link expiry")
        access_days = _bounded_days(body.get("access_days"), None, MAX_ACCESS_DAYS, "Access period")
        show_updates = body.get("show_updates", True)
        if not isinstance(show_updates, bool):
            raise HTTPException(status_code=422, detail="show_updates must be true or false.")
        document_ids = await _validated_document_ids(booking, user, _clean_doc_ids(body.get("document_ids")))
        existing = await _active_share(booking_id, partner_email)
        if existing and not regenerate:
            return {"success": True, "message": "Partner link ready", "data": _present_owner_share(existing)}
        if existing and regenerate:
            # Only unclaimed invites with the same restriction are rotated; partners who already joined keep access.
            await coll.update_many({"booking_id": booking_id, "revoked": {"$ne": True}, "claimed_by": None,
                                    "partner_email": existing.get("partner_email")},
                                   {"$set": {"revoked": True, "revoked_at": _now().isoformat()}})
        if existing and regenerate:
            # Rotating keeps what the owner already decided unless they send new choices.
            if "show_updates" not in body:
                show_updates = existing.get("show_updates", True)
            if "document_ids" not in body:
                document_ids = list(existing.get("document_ids") or [])
            if body.get("access_days") in (None, ""):
                access_days = existing.get("access_days")
        share = await _create_share(booking, user, partner_email, link_days=link_days, access_days=access_days,
                                    show_updates=show_updates, document_ids=document_ids)
        return {"success": True, "message": "Partner link generated", "data": _present_owner_share(share)}

    @router.get("/bookings/{booking_id}/partner-share")
    async def get_partner_share(booking_id: str, user: dict = Depends(get_current_user)):
        await _owned_booking(booking_id, user)
        share = await _latest_unclaimed_share(booking_id)
        return {"success": True, "message": "OK", "data": _present_owner_share(share) if share else None}

    @router.delete("/bookings/{booking_id}/partner-share")
    async def revoke_partner_share(booking_id: str, user: dict = Depends(get_current_user)):
        """Stop sharing entirely: every invite link dies and every partner loses access."""
        await _owned_booking(booking_id, user)
        res = await coll.update_many({"booking_id": booking_id, "revoked": {"$ne": True}},
                                     {"$set": {"revoked": True, "revoked_at": _now().isoformat()}})
        grants = await _revoke_grants({"booking_id": booking_id})
        return {"success": True, "message": "Partner link revoked",
                "data": {"revoked": res.modified_count, "access_revoked": grants}}

    @router.delete("/bookings/{booking_id}/partner-invitations/{share_id}")
    async def revoke_partner_invitation(booking_id: str, share_id: str, user: dict = Depends(get_current_user)):
        """Only the booking owner may revoke an invitation for that booking."""
        await _owned_booking(booking_id, user)
        now = _now().isoformat()
        result = await coll.update_one({"id": share_id, "booking_id": booking_id, "revoked": {"$ne": True}},
            {"$set": {"revoked": True, "revoked_at": now, "status_changed_at": now, "status_changed_by": user.get("id")}})
        if not result.modified_count:
            raise HTTPException(status_code=404, detail="Invitation not found")
        await _revoke_grants({"share_id": share_id})
        await db["erp_partner_share_audit"].insert_one({"event_id": "PSA-" + uuid.uuid4().hex[:12].upper(), "booking_id": booking_id, "share_id": share_id, "actor_user_id": user.get("id"), "event": "invitation_revoked", "created_at": now})
        return {"success": True, "message": "Invitation revoked", "data": {"revoked": True}}

    @router.get("/bookings/{booking_id}/partner-access")
    async def list_partner_access(booking_id: str, user: dict = Depends(get_current_user)):
        await _owned_booking(booking_id, user)
        rows = [_present_owner_access(g) async for g in
                access_coll.find({"booking_id": booking_id, "revoked": {"$ne": True}}, {"_id": 0}).sort("granted_at", 1)]
        return {"success": True, "message": "OK", "data": rows}

    @router.delete("/bookings/{booking_id}/partner-access/{access_id}")
    async def revoke_partner_access(booking_id: str, access_id: str, user: dict = Depends(get_current_user)):
        await _owned_booking(booking_id, user)
        n = await _revoke_grants({"id": access_id, "booking_id": booking_id})
        if not n:
            raise HTTPException(status_code=404, detail="Partner access not found")
        logger.info("partner access %s revoked by owner %s", access_id, user.get("id"))
        return {"success": True, "message": "Partner access removed", "data": {"revoked": n}}

    # ---------------------------------------------------------- public link status
    @router.get("/public/partner-share/{token}")
    async def public_partner_share(token: str, request: Request):
        """Tells the login gate whether the link is worth logging in for. Returns NO booking data."""
        _rate_limit(request)
        if not TOKEN_RE.match(token or ""):
            raise HTTPException(status_code=404, detail="This link is not valid.")
        share = await coll.find_one({"token": token}, {"_id": 0, "revoked": 1, "expires_at": 1, "partner_email": 1, "parent_access_id": 1})
        if not share:
            raise HTTPException(status_code=404, detail="This link is not valid.")
        if not _is_active(share):
            raise HTTPException(status_code=410, detail="This link has expired or was withdrawn by the customer.")
        if share.get("parent_access_id"):
            parent = await access_coll.find_one({"id": share["parent_access_id"]}, {"_id": 0})
            if not await _grant_chain_active(parent):
                raise HTTPException(status_code=410, detail="The partner who shared this link no longer has access.")
        return {"success": True, "message": "OK",
                "data": {"valid": True, "requires_login": True, "restricted": bool(share.get("partner_email"))}}

    # ------------------------------------------------------------ partner endpoints
    @router.post("/partner/shares/redeem")
    async def redeem_partner_share(request: Request, body: dict = Body(default={}), user: dict = Depends(get_current_user)):
        """Authenticated partner presents the link token; on success THIS account gets an access grant."""
        _redeem_rate_limit(request)
        _require_customer_account(user)
        token = str((body or {}).get("token") or "")
        if not TOKEN_RE.match(token):
            raise HTTPException(status_code=404, detail="This link is not valid.")
        share = await coll.find_one({"token": token}, {"_id": 0})
        if not share:
            raise HTTPException(status_code=404, detail="This link is not valid.")
        if not _is_active(share):
            raise HTTPException(status_code=410, detail="This link has expired or was withdrawn by the customer.")
        booking = await db["erp_bookings"].find_one({"id": share["booking_id"]}, {"_id": 0})
        if not booking:
            raise HTTPException(status_code=404, detail="This link is not valid.")
        parent_grant = None
        if share.get("parent_access_id"):
            parent_grant = await access_coll.find_one({"id": share["parent_access_id"]}, {"_id": 0})
            if (not parent_grant or parent_grant.get("partner_user_id") != share.get("delegated_by")
                    or parent_grant.get("booking_id") != booking.get("id")
                    or not await _grant_chain_active(parent_grant)):
                raise HTTPException(status_code=410, detail="The partner who shared this link no longer has access.")

        # The owner opening their own link just goes to their bookings - no grant is created.
        owners = await _owner_ids(user)
        if booking.get("customer_id") in owners:
            return {"success": True, "message": "OK", "data": {"is_owner": True}}

        user_id = user.get("id")
        user_email = _norm_email(user.get("email"))
        restricted_to = _norm_email(share.get("partner_email"))
        if restricted_to and restricted_to != user_email:
            raise HTTPException(status_code=403, detail="This link was shared with a different email address. Please sign in with the account it was sent to.")

        # Atomically claim the link. Only one account can ever win this update.
        claim = await coll.update_one(
            {"token": token, "revoked": {"$ne": True}, "claimed_by": None},
            {"$set": {"claimed_by": user_id, "claimed_by_email": user_email, "claimed_at": _now().isoformat()}})
        if not claim.modified_count:
            current = await coll.find_one({"token": token}, {"_id": 0, "claimed_by": 1, "revoked": 1})
            if not current or current.get("revoked"):
                raise HTTPException(status_code=410, detail="This link has expired or was withdrawn by the customer.")
            if current.get("claimed_by") != user_id:
                logger.warning("partner link %s presented by non-claimant account %s", share["id"], user_id)
                raise HTTPException(status_code=403, detail="This link has already been used by another account. Ask the customer to send you your own link.")

        grant = await access_coll.find_one({"booking_id": booking["id"], "partner_user_id": user_id}, {"_id": 0})
        if grant and grant.get("revoked"):
            raise HTTPException(status_code=403, detail="Your access to this booking was withdrawn by the customer.")
        if grant and not _grant_active(grant):
            raise HTTPException(status_code=403, detail="Your access to this booking has ended. Ask the customer for a new link.")
        if not grant:
            access_days = share.get("access_days")
            grant = {
                "id": "PAC-" + uuid.uuid4().hex[:10].upper(),
                "booking_id": booking["id"],
                "share_id": share["id"],
                "owner_customer_id": share.get("customer_id"),
                "partner_user_id": user_id,
                "partner_email": user_email,
                "partner_name": user.get("name"),
                "granted_at": _now().isoformat(),
                "invitation_claimed_at": share.get("claimed_at") or _now().isoformat(),
                "account_created_at": user.get("created_at") or user.get("createdAt"),
                "active": True,
                "revoked": False,
                "show_updates": share.get("show_updates", True),
                "document_ids": list(share.get("document_ids") or []),
                "access_expires_at": (_now() + timedelta(days=access_days)).isoformat() if access_days else None,
                "parent_access_id": share.get("parent_access_id"),
                "delegated_by": share.get("delegated_by"),
                "hierarchy_depth": int((parent_grant or {}).get("hierarchy_depth", 0)) + 1 if parent_grant else 0,
                "root_share_id": (parent_grant or {}).get("root_share_id") or share.get("id"),
            }
            try:
                await access_coll.insert_one({**grant})
            except Exception:  # unique (booking, user) index: a concurrent redeem already created it
                grant = await access_coll.find_one({"booking_id": booking["id"], "partner_user_id": user_id}, {"_id": 0})
                if not grant or grant.get("revoked"):
                    raise HTTPException(status_code=403, detail="Your access to this booking was withdrawn by the customer.")
            else:
                logger.info("partner access %s granted to %s for booking %s", grant["id"], user_id, booking["id"])
        return {"success": True, "message": "Booking shared with you", "data": {"is_owner": False, **await _summary(booking, grant)}}

    @router.post("/partner/shared-bookings/{access_id}/share")
    async def delegate_shared_booking(access_id: str, body: dict = Body(default={}), user: dict = Depends(get_current_user)):
        """Create a child invite only within the permissions explicitly granted by the original customer."""
        _require_customer_account(user)
        parent = await _parent_grant_for_user(access_id, user)
        parent = await _effective_permissions(parent)
        body = body or {}
        partner_email = _norm_email(body.get("partner_email"))
        if partner_email and not EMAIL_RE.match(partner_email):
            raise HTTPException(status_code=422, detail="Enter a valid sub-partner email address.")
        requested_docs = _clean_doc_ids(body.get("document_ids", []))
        allowed_docs = set(parent.get("document_ids") or [])
        if any(doc_id not in allowed_docs for doc_id in requested_docs):
            raise HTTPException(status_code=403, detail="You cannot share documents that the original customer did not authorize for you.")
        show_updates = body.get("show_updates", bool(parent.get("show_updates", True)))
        if not isinstance(show_updates, bool):
            raise HTTPException(status_code=422, detail="show_updates must be true or false.")
        if show_updates and not parent.get("show_updates", True):
            raise HTTPException(status_code=403, detail="The original customer did not authorize sharing booking updates.")
        booking = await db["erp_bookings"].find_one({"id": parent["booking_id"]}, {"_id": 0})
        if not booking:
            raise HTTPException(status_code=404, detail="Shared booking not found")
        # Link lifetime can never exceed the remaining lifetime of this parent grant.
        parent_expiry = _parse_dt(parent.get("access_expires_at"))
        link_expiry = _now() + timedelta(days=min(_ttl_days(), MAX_LINK_DAYS))
        if parent_expiry and parent_expiry < link_expiry:
            link_expiry = parent_expiry
        if link_expiry <= _now():
            raise HTTPException(status_code=410, detail="Your access to this booking has ended.")
        token = secrets.token_urlsafe(32)
        link_path = f"{LINK_PATH_PREFIX}{token}"
        base = _base_url()
        share = {
            "id": "PSH-" + uuid.uuid4().hex[:10].upper(), "booking_id": booking["id"],
            "booking_no": booking.get("booking_no") or booking["id"],
            "customer_id": parent.get("owner_customer_id"), "token": token, "link_path": link_path,
            "share_url": f"{base}{link_path}" if base else None, "partner_email": partner_email,
            "created_at": _now().isoformat(), "created_by": user.get("id"), "delegated_by": user.get("id"),
            "parent_access_id": parent["id"], "root_share_id": parent.get("root_share_id") or parent.get("share_id"),
            "hierarchy_depth": int(parent.get("hierarchy_depth", 0)) + 1,
            "expires_at": link_expiry.isoformat(), "revoked": False, "claimed_by": None,
            "view_count": 0, "show_updates": show_updates, "document_ids": requested_docs,
            "access_days": None,
        }
        await coll.insert_one({**share})
        return {"success": True, "message": "Sub-partner link generated", "data": _present_owner_share(share)}

    @router.delete("/partner/shared-bookings/{access_id}/share/{share_id}")
    async def revoke_delegated_share(access_id: str, share_id: str, user: dict = Depends(get_current_user)):
        """A partner may revoke only a sub-partner invite they personally created from their own grant."""
        parent = await _parent_grant_for_user(access_id, user)
        result = await coll.update_one({
            "id": share_id, "parent_access_id": parent["id"], "delegated_by": user.get("id"),
            "revoked": {"$ne": True},
        }, {"$set": {"revoked": True, "revoked_at": _now().isoformat()}})
        if not result.modified_count:
            raise HTTPException(status_code=404, detail="Sub-partner link not found")
        # If the invite was already redeemed, revoke its grant too. Descendants become invalid via chain checks.
        await _revoke_grants({"share_id": share_id})
        return {"success": True, "message": "Sub-partner link revoked", "data": {"revoked": True}}

    @router.patch("/partner/shared-bookings/{access_id}/my-access")
    async def update_my_partner_access(access_id: str, body: dict = Body(default={}), user: dict = Depends(get_current_user)):
        """A partner can deactivate or leave only their own grant; this never revokes an invitation or another account."""
        _require_customer_account(user)
        if not ACCESS_ID_RE.match(access_id or ""):
            raise HTTPException(status_code=404, detail="Shared booking not found")
        grant = await access_coll.find_one({"id": access_id, "partner_user_id": user.get("id"), "revoked": {"$ne": True}}, {"_id": 0})
        if not grant:
            raise HTTPException(status_code=404, detail="Shared booking not found")
        body = body or {}
        action = body.get("action")
        if action not in {"deactivate", "restore", "leave"}:
            raise HTTPException(status_code=422, detail="Choose deactivate, restore, or leave.")
        now = _now().isoformat()
        if action == "leave":
            changes = {"revoked": True, "revoked_at": now, "status_changed_at": now, "status_changed_by": user.get("id")}
        else:
            changes = {"active": action == "restore", "status_changed_at": now, "status_changed_by": user.get("id")}
        await access_coll.update_one({"id": access_id, "partner_user_id": user.get("id")}, {"$set": changes})
        await db["erp_partner_share_audit"].insert_one({"event_id": "PSA-" + uuid.uuid4().hex[:12].upper(), "booking_id": grant.get("booking_id"), "access_id": access_id, "actor_user_id": user.get("id"), "event": "partner_" + action, "created_at": now})
        return {"success": True, "message": "Your partner access was updated", "data": {"action": action}}

    @router.get("/partner/shared-bookings")
    async def my_shared_bookings(response: Response, user: dict = Depends(get_current_user)):
        _no_store(response)
        _require_customer_account(user)
        out = []
        async for grant in access_coll.find({"partner_user_id": user.get("id"), "revoked": {"$ne": True}}, {"_id": 0}).sort("granted_at", -1):
            if not await _grant_chain_active(grant):
                continue
            booking = await db["erp_bookings"].find_one({"id": grant["booking_id"]}, {"_id": 0})
            if booking:
                await access_coll.update_one({"id": grant["id"]}, {"$set": {"last_viewed_at": _now().isoformat()}})
                out.append(await _summary(booking, grant))
        return {"success": True, "message": "OK", "data": out}

    @router.get("/partner/shared-bookings/{access_id}")
    async def my_shared_booking(access_id: str, response: Response, user: dict = Depends(get_current_user)):
        _no_store(response)
        _require_customer_account(user)
        grant = await _my_grant(access_id, user)
        booking = await db["erp_bookings"].find_one({"id": grant["booking_id"]}, {"_id": 0})
        if not booking:
            raise HTTPException(status_code=404, detail="Shared booking not found")
        now = _now().isoformat()
        await access_coll.update_one({"id": grant["id"]}, {"$set": {"last_viewed_at": now}})
        await coll.update_one({"id": grant.get("share_id")}, {"$inc": {"view_count": 1}, "$set": {"last_viewed_at": now}})
        return {"success": True, "message": "OK", "data": await _detail(booking, grant)}

    @router.get("/partner/shared-bookings/{access_id}/documents/{doc_id}")
    async def my_shared_document(access_id: str, doc_id: str, response: Response, user: dict = Depends(get_current_user)):
        """Download ONE document. Three independent checks: my active grant, the owner ticked this document for
        me, and the document still belongs to the owner. Anything else is a plain 404."""
        _no_store(response)
        _require_customer_account(user)
        grant = await _my_grant(access_id, user)
        grant = await _effective_permissions(grant)
        if not DOC_ID_RE.match(doc_id or "") or doc_id not in (grant.get("document_ids") or []):
            raise HTTPException(status_code=404, detail="Document not found")
        doc = await db["erp_documents"].find_one({"id": doc_id, "customer_id": grant.get("owner_customer_id")}, {"_id": 0})
        if not doc or not doc.get("file_data"):
            raise HTTPException(status_code=404, detail="Document not found")
        logger.info("partner %s downloaded shared document %s via %s", user.get("id"), doc_id, access_id)
        return {"success": True, "message": "OK", "data": {
            "doc_id": doc_id, "file_name": doc.get("file_name") or doc.get("title") or "document",
            "mime_type": doc.get("mime_type"), "file_data": doc.get("file_data")}}

    # --------------------------------------------------- owner: dashboard + permissions
    @router.get("/bookings/{booking_id}/partner-share/documents")
    async def shareable_documents(booking_id: str, user: dict = Depends(get_current_user)):
        booking = await _owned_booking(booking_id, user)
        rows = [{"doc_id": d.get("id"), "name": d.get("title") or d.get("name") or d.get("file_name") or "Document",
                 "file_name": d.get("file_name"), "uploaded_date": d.get("uploaded_date")}
                for d in await _shareable_documents(booking, user) if d.get("file_name") or d.get("title") or d.get("name")]
        return {"success": True, "message": "OK", "data": rows}

    @router.patch("/bookings/{booking_id}/partner-access/{access_id}")
    async def update_partner_access(booking_id: str, access_id: str, body: dict = Body(default={}), user: dict = Depends(get_current_user)):
        """Owner changes what one partner may see. Takes effect immediately on the next partner request."""
        booking = await _owned_booking(booking_id, user)
        grant = await access_coll.find_one({"id": access_id, "booking_id": booking_id, "revoked": {"$ne": True}}, {"_id": 0})
        if not grant:
            raise HTTPException(status_code=404, detail="Partner access not found")
        body = body or {}
        changes = {}
        if "active" in body:
            if not isinstance(body["active"], bool):
                raise HTTPException(status_code=422, detail="active must be true or false.")
            changes["active"] = body["active"]
            changes["status_changed_at"] = _now().isoformat()
            changes["status_changed_by"] = user.get("id")
        if "show_updates" in body:
            if not isinstance(body["show_updates"], bool):
                raise HTTPException(status_code=422, detail="show_updates must be true or false.")
            changes["show_updates"] = body["show_updates"]
        if "document_ids" in body:
            changes["document_ids"] = await _validated_document_ids(booking, user, _clean_doc_ids(body["document_ids"]))
        if "access_days" in body:
            days = _bounded_days(body["access_days"], None, MAX_ACCESS_DAYS, "Access period")
            changes["access_expires_at"] = (_now() + timedelta(days=days)).isoformat() if days else None
        if changes:
            await access_coll.update_one({"id": access_id}, {"$set": changes})
            await db["erp_partner_share_audit"].insert_one({"event_id": "PSA-" + uuid.uuid4().hex[:12].upper(), "booking_id": booking_id, "access_id": access_id, "actor_user_id": user.get("id"), "event": "access_updated", "changes": {k: v for k, v in changes.items() if k not in {"document_ids"}}, "created_at": _now().isoformat()})
        grant = await access_coll.find_one({"id": access_id}, {"_id": 0})
        return {"success": True, "message": "Partner permissions updated", "data": _present_owner_access(grant)}

    @router.get("/partner/my-shares")
    async def my_shares(response: Response, user: dict = Depends(get_current_user)):
        """Dashboard for the sharing customer: each booking they shared, its live link and the partners who joined."""
        _no_store(response)
        owners = await _owner_ids(user)
        if not owners:
            return {"success": True, "message": "OK", "data": []}
        booking_ids = []
        async for g in access_coll.find({"owner_customer_id": {"$in": list(owners)}}, {"_id": 0, "booking_id": 1}):
            booking_ids.append(g["booking_id"])
        async for sh in coll.find({"customer_id": {"$in": list(owners)}}, {"_id": 0, "booking_id": 1}):
            booking_ids.append(sh["booking_id"])
        out = []
        for bid in dict.fromkeys(booking_ids):
            booking = await db["erp_bookings"].find_one({"id": bid, "customer_id": {"$in": list(owners)}}, {"_id": 0})
            if not booking:
                continue
            link = await _latest_unclaimed_share(bid)
            partners = []
            async for grant in access_coll.find({"booking_id": bid}, {"_id": 0}).sort("granted_at", 1):
                row = _present_owner_access(grant)
                row["invitation_status"] = "Revoked" if grant.get("revoked") else "Claimed"
                partners.append(row)
            invitations = []
            async for invite in coll.find({"booking_id": bid}, {"_id": 0}).sort("created_at", -1):
                if invite.get("claimed_by"):
                    invite_status = "Claimed"
                elif invite.get("revoked"):
                    invite_status = "Revoked"
                elif not _is_active(invite):
                    invite_status = "Expired"
                else:
                    invite_status = "Pending"
                account = await db["users"].find_one({"id": invite.get("claimed_by")}, {"_id": 0, "name": 1, "full_name": 1, "created_at": 1, "createdAt": 1}) if invite.get("claimed_by") else None
                invitations.append({"invite_id": invite.get("id"), "partner_email": invite.get("partner_email"),
                    "partner_name": (account or {}).get("name") or (account or {}).get("full_name"),
                    "invitation_status": invite_status,
                    "registration_status": "Registered" if invite.get("claimed_by") else "Not registered / not claimed",
                    "invitation_claimed_at": invite.get("claimed_at"), "account_created_at": (account or {}).get("created_at") or (account or {}).get("createdAt"),
                    "last_activity_at": next((g.get("last_viewed_at") for g in partners if g.get("partner_email") == invite.get("partner_email")), None),
                    "created_at": invite.get("created_at"), "access_expires_at": invite.get("expires_at"),
                    "access_status": "Revoked" if invite.get("revoked") else "Expired" if invite_status == "Expired" else "Inactive" if any(g.get("partner_email") == invite.get("partner_email") and g.get("active") is False for g in partners) else "Active" if invite.get("claimed_by") else "Pending",
                    "revoked": bool(invite.get("revoked")), "is_delegated": bool(invite.get("parent_access_id")), "parent_access_id": invite.get("parent_access_id")})
            out.append({"booking_id": bid, "booking_no": booking.get("booking_no") or bid,
                        "service": booking.get("service") or booking.get("service_name"), "status": booking.get("status"),
                        "booking_date": _booking_date(booking),
                        "link": _present_owner_share(link) if link else None, "partners": partners, "invitations": invitations})
        return {"success": True, "message": "OK", "data": out}

    return router
