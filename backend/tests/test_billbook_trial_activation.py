"""Trial-activation tests against a MongoDB-compatible test database (mongomock-motor).

Unique indexes are created with the SAME helpers the server uses at startup, so duplicate-key
behaviour is real. No production data, network or Razorpay account is involved.
"""
import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import HTTPException  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402
from pymongo.errors import DuplicateKeyError  # noqa: E402

import billbook_subscription as bs  # noqa: E402
from index_utils import ensure_index, repair_legacy_unique_index  # noqa: E402
from test_billbook_subscription import FakeRazorpay, SECRET, VALID  # noqa: E402

UTC = timezone.utc
TRIAL_PLAN = {"id": "trial_1", "name": "7 Day Trial", "plan_type": "free_trial", "price": 0, "duration_days": 7,
              "duration_months": 0, "active": True, "published": True, "eligibility_rules": {"one_per_customer": True}}
A = {"id": "cust-A", "role": "customer"}
B = {"id": "cust-B", "role": "customer"}


class ProxyDB:
    """Wraps a real (mock) database so a test can intercept insert_one on one collection."""

    def __init__(self, db, intercept=None):
        self.db, self.intercept = db, intercept

    def __getitem__(self, name):
        col = self.db[name]
        if name == "billbook_subscriptions" and self.intercept:
            return self.intercept(col)
        return col


class FlakyCollection:
    def __init__(self, col, on_insert):
        self._col, self._on_insert = col, on_insert

    def __getattr__(self, name):
        return getattr(self._col, name)

    async def insert_one(self, doc):
        await self._on_insert(self._col, doc)


async def make_db(legacy_order_index=False):
    db = AsyncMongoMockClient()["test_trials"]
    await db["billbook_plans"].insert_one(dict(TRIAL_PLAN))
    subs = db["billbook_subscriptions"]
    if legacy_order_index:
        await subs.create_index([("order_id", 1)], unique=True, name="order_id_1")  # the legacy, non-sparse shape
    else:
        await ensure_index(subs, "order_id", unique=True, sparse=True, name="uniq_billbook_subscription_order")
    await ensure_index(subs, [("customer_id", 1), ("subscription_type", 1)], unique=True,
                       name="uniq_billbook_one_trial_per_customer", partial_filter={"subscription_type": "free_trial"})
    return db


def build(db, razorpay=None):
    factory = (lambda: (razorpay, "rzp_test_key", SECRET)) if razorpay else (lambda: (None, None, None))
    router = bs.build_billbook_subscription_router(db, lambda: None, factory)

    def ep(path, method):
        return next(r.endpoint for r in router.routes if r.path == path and method in r.methods)
    return ep


def activate(ep, user, body=None):
    return ep("/api/billbook/trials/activate", "POST")(body or {"plan_id": "trial_1"}, user)


async def trials(db, cid=None):
    q = {"subscription_type": "free_trial"}
    if cid:
        q["customer_id"] = cid
    return [d async for d in db["billbook_subscriptions"].find(q)]


class TrialActivationTests(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_01_eligible_customer_activates(self):
        async def go():
            db = await make_db(); ep = build(db)
            res = await activate(ep, A)
            self.assertTrue(res["success"] and res["data"]["trial"])
            self.assertNotIn("resumed", res["data"])
            self.assertEqual(len(await trials(db, "cust-A")), 1)
        self.run_async(go())

    def test_trial_record_persists_plan_customer_dates_and_active_status(self):
        async def go():
            db = await make_db(); ep = build(db)
            before = datetime.now(UTC)
            res = await activate(ep, A)
            after = datetime.now(UTC)
            stored = await db["billbook_subscriptions"].find_one({"customer_id": "cust-A", "subscription_type": "free_trial"})
            self.assertEqual(stored["plan_id"], "trial_1")
            self.assertEqual(stored["plan_name"], "7 Day Trial")
            self.assertEqual(stored["status"], "active")
            self.assertEqual(stored["amount"], 0)
            self.assertEqual(stored["activated_by"], "cust-A")
            self.assertGreaterEqual(datetime.fromisoformat(stored["started_at"]), before)
            self.assertLessEqual(datetime.fromisoformat(stored["started_at"]), after)
            self.assertEqual(datetime.fromisoformat(stored["expires_at"]), datetime.fromisoformat(stored["started_at"]) + timedelta(days=7))
            self.assertEqual(res["data"]["subscription"]["id"], stored["id"])
        self.run_async(go())

    def test_02_03_repeat_and_active_trial_return_existing_status(self):
        async def go():
            db = await make_db(); ep = build(db)
            first = await activate(ep, A)
            second = await activate(ep, A)
            self.assertTrue(second["data"]["resumed"])
            self.assertEqual(second["data"]["subscription"]["id"], first["data"]["subscription"]["id"])
            self.assertEqual(len(await trials(db)), 1)
        self.run_async(go())

    def test_04_consumed_trial_is_rejected_with_403_and_code(self):
        async def go():
            db = await make_db(); ep = build(db)
            await db["billbook_subscriptions"].insert_one({
                "id": "old", "customer_id": "cust-A", "subscription_type": "free_trial", "status": "active",
                "created_at": "2026-01-01T00:00:00+00:00", "expires_at": (datetime.now(UTC) - timedelta(days=30)).isoformat()})
            with self.assertRaises(HTTPException) as cm:
                await activate(ep, A)
            self.assertEqual(cm.exception.status_code, 403)
            self.assertEqual(cm.exception.detail["code"], "TRIAL_ALREADY_CLAIMED")
            self.assertEqual(len(await trials(db)), 1)
        self.run_async(go())

    def test_05_simultaneous_requests_create_one_trial(self):
        async def go():
            db = await make_db(); ep = build(db)
            results = await asyncio.gather(*[activate(ep, A) for _ in range(8)], return_exceptions=True)
            self.assertFalse([r for r in results if isinstance(r, Exception)], results)
            self.assertEqual(len(await trials(db, "cust-A")), 1)
            self.assertEqual(len({r["data"]["subscription"]["id"] for r in results}), 1)
        self.run_async(go())

    def test_05b_different_customers_do_not_block_each_other(self):
        async def go():
            db = await make_db(); ep = build(db)
            ra, rb = await asyncio.gather(activate(ep, A), activate(ep, B))
            self.assertTrue(ra["success"] and rb["success"])
            self.assertEqual(len(await trials(db)), 2)
        self.run_async(go())

    def test_06_duplicate_key_from_race_returns_existing_trial(self):
        async def go():
            db = await make_db()

            async def competing(col, doc):  # another request commits first, then ours collides
                await col.insert_one({**doc, "id": "BBT-WINNER"})
                raise DuplicateKeyError("E11000 duplicate key error index: uniq_billbook_one_trial_per_customer", 11000,
                                        {"keyPattern": {"customer_id": 1, "subscription_type": 1}})
            ep = build(ProxyDB(db, lambda c: FlakyCollection(c, competing)))
            res = await activate(ep, A)
            self.assertTrue(res["data"]["resumed"])
            self.assertEqual(res["data"]["subscription"]["id"], "BBT-WINNER")
            self.assertEqual(len(await trials(db)), 1)
        self.run_async(go())

    def test_06b_duplicate_key_with_no_record_is_a_structured_retryable_409(self):
        async def go():
            db = await make_db()

            async def always(col, doc):
                raise DuplicateKeyError("E11000 duplicate key error", 11000)
            ep = build(ProxyDB(db, lambda c: FlakyCollection(c, always)))
            with self.assertRaises(HTTPException) as cm:
                await activate(ep, A)
            self.assertEqual(cm.exception.status_code, 409)
            self.assertEqual(cm.exception.detail["code"], "TRIAL_ACTIVATION_IN_PROGRESS")
            self.assertEqual(cm.exception.detail["retry_after_seconds"], 3)
        self.run_async(go())

    def test_07_pending_paid_records_neither_block_nor_get_modified(self):
        async def go():
            db = await make_db(); ep = build(db)
            for i, st in enumerate(("pending", "processing", "created")):
                await db["billbook_subscriptions"].insert_one({
                    "id": f"p{i}", "customer_id": "cust-A", "subscription_type": "paid", "status": st,
                    "order_id": f"order_{i}", "created_at": "2020-01-01T00:00:00+00:00"})
            res = await activate(ep, A)
            self.assertTrue(res["success"])
            statuses = sorted([d["status"] async for d in db["billbook_subscriptions"].find({"subscription_type": "paid"})])
            self.assertEqual(statuses, ["created", "pending", "processing"])
        self.run_async(go())

    def test_08_failed_write_leaves_no_lock_and_retry_succeeds(self):
        async def go():
            db = await make_db()
            calls = {"n": 0}

            async def flaky(col, doc):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("mongo went away")
                await col.insert_one(doc)
            ep = build(ProxyDB(db, lambda c: FlakyCollection(c, flaky)))
            with self.assertRaises(HTTPException) as cm:
                await activate(ep, A)
            self.assertEqual(cm.exception.status_code, 500)
            self.assertEqual(cm.exception.detail["code"], "TRIAL_ACTIVATION_FAILED")
            self.assertNotIn("mongo went away", str(cm.exception.detail))
            self.assertEqual(len(await trials(db)), 0)
            self.assertTrue((await activate(ep, A))["success"])
            self.assertEqual(len(await trials(db)), 1)
        self.run_async(go())

    def test_09_unauthenticated_and_non_customer_rejected(self):
        async def go():
            db = await make_db(); ep = build(db)
            for user, code in ((None, 401), ({}, 401), ({"id": "x", "role": "admin"}, 403), ({"role": "customer"}, 401)):
                with self.assertRaises(HTTPException) as cm:
                    await activate(ep, user)
                self.assertEqual(cm.exception.status_code, code)
            self.assertEqual(len(await trials(db)), 0)
        self.run_async(go())

    def test_10_body_cannot_name_another_customer(self):
        async def go():
            db = await make_db(); ep = build(db)
            await activate(ep, A, {"plan_id": "trial_1", "customer_id": "cust-B", "user_id": "cust-B"})
            self.assertEqual(len(await trials(db, "cust-A")), 1)
            self.assertEqual(len(await trials(db, "cust-B")), 0)
        self.run_async(go())

    def test_11_optional_gstin_is_not_required_or_used(self):
        async def go():
            db = await make_db(); ep = build(db)
            for gstin in (None, "", "   "):
                db2 = await make_db(); ep2 = build(db2)
                body = {"plan_id": "trial_1"} if gstin is None else {"plan_id": "trial_1", "gstin": gstin}
                self.assertTrue((await activate(ep2, A, body))["success"])
        self.run_async(go())

    def test_12_paid_checkout_unchanged_while_trial_active(self):
        async def go():
            db = await make_db()
            await db["billbook_plans"].insert_one({"id": "1_month", "price": 200})
            rz = FakeRazorpay(); ep = build(db, rz)
            await activate(ep, A)
            out = await ep("/api/billbook/subscription/checkout", "POST")(dict(VALID), A)
            self.assertTrue(out["success"])
            self.assertEqual(len(rz.orders), 1)
            self.assertEqual(rz.orders[0]["amount"], 20000)
            pending = [d async for d in db["billbook_subscriptions"].find({"order_id": {"$type": "string"}})]
            self.assertEqual(pending[0]["status"], "pending")  # paid plan is NOT active before verification
            self.assertEqual(len(rz.orders), 1)
            self.assertEqual(len(await trials(db)), 1)
        self.run_async(go())

    def test_12b_trial_creates_no_razorpay_order_or_payment(self):
        async def go():
            db = await make_db(); rz = FakeRazorpay(); ep = build(db, rz)
            await activate(ep, A)
            self.assertEqual(rz.orders, [])
            self.assertEqual(await db["erp_payments"].count_documents({}), 0)
        self.run_async(go())

    def test_13_timeout_then_retry_does_not_create_second_trial(self):
        async def go():
            db = await make_db(); ep = build(db)
            try:  # request 1 commits, the client gives up before reading the reply
                await asyncio.wait_for(asyncio.shield(activate(ep, A)), timeout=0.0000001)
            except asyncio.TimeoutError:
                pass
            await asyncio.sleep(0.2)
            retry = await activate(ep, A)
            self.assertEqual(len(await trials(db, "cust-A")), 1)
            self.assertTrue(retry["success"])
        self.run_async(go())


class LegacyOrderIndexTests(unittest.TestCase):
    def test_root_cause_legacy_non_sparse_order_index_blocks_second_customer_then_self_heals(self):
        async def go():
            db = await make_db(legacy_order_index=True)
            # Another record without an order_id already holds the single allowed order_id=null slot.
            await db["billbook_subscriptions"].insert_one({"id": "BBT-OLD", "customer_id": "cust-B", "subscription_type": "free_trial",
                                                            "status": "active", "expires_at": (datetime.now(UTC) + timedelta(days=3)).isoformat()})
            ep = build(db)
            res = await activate(ep, A)  # previously: 409 "Another trial request is still being processed"
            self.assertTrue(res["success"])
            self.assertEqual(len(await trials(db, "cust-A")), 1)
            idx = {i["name"]: i for i in await db["billbook_subscriptions"].list_indexes().to_list(length=None)}
            self.assertNotIn("order_id_1", idx)
            self.assertTrue(idx["uniq_billbook_subscription_order"]["sparse"])
        asyncio.run(go())

    def test_repair_keeps_real_order_id_uniqueness_and_is_idempotent(self):
        async def go():
            db = await make_db(legacy_order_index=True)
            col = db["billbook_subscriptions"]
            await col.insert_one({"id": "p1", "customer_id": "c1", "order_id": "order_1"})
            self.assertEqual(await repair_legacy_unique_index(col, "order_id", name="uniq_billbook_subscription_order"), "repaired")
            self.assertEqual(await repair_legacy_unique_index(col, "order_id", name="uniq_billbook_subscription_order"), "already_correct")
            await col.insert_one({"id": "t1"}); await col.insert_one({"id": "t2"})  # many trials w/o order are fine now
            with self.assertRaises(DuplicateKeyError):
                await col.insert_one({"id": "p2", "customer_id": "c2", "order_id": "order_1"})
            self.assertEqual(await col.count_documents({}), 3)  # no data deleted or changed
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main(verbosity=2)
