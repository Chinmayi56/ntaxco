"""Paid-checkout hardening tests (HTTP 500 on POST /api/billbook/subscription/checkout).

Run against a MongoDB-compatible test database (mongomock-motor) with the SAME index helpers the
server uses at startup, and a mocked Razorpay client. No network, production data or real payment.
"""
import asyncio
import logging
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import HTTPException  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402
from pymongo.errors import DuplicateKeyError  # noqa: E402

import billbook_subscription as bs  # noqa: E402
from index_utils import ensure_index, repair_legacy_trial_index  # noqa: E402
from test_billbook_subscription import SECRET, VALID, sign  # noqa: E402
from test_billbook_trial_activation import FlakyCollection, ProxyDB, TRIAL_PLAN  # noqa: E402

USER = {"id": "cust-1", "role": "customer", "email": "asha@example.com"}
OTHER = {"id": "cust-2", "role": "customer", "email": "ravi@example.com"}
CHECKOUT = "/api/billbook/subscription/checkout"
VERIFY = "/api/billbook/subscription/verify"
CANCEL = "/api/billbook/subscription/cancel-attempt"


class Gateway:
    """Mocked Razorpay client (orders + payments)."""

    def __init__(self, fail_create=False):
        self.orders, self.payments, self.fail_create = [], {}, fail_create
        self.order = types.SimpleNamespace(create=self._create, all=self._all)
        self.payment = types.SimpleNamespace(fetch=self._fetch)

    def _create(self, data):
        if self.fail_create:
            raise RuntimeError("gateway down")
        order = {**data, "id": f"order_{len(self.orders) + 1}", "status": "created"}
        self.orders.append(order)
        return {"id": order["id"]}

    def _all(self, params):
        return {"items": [o for o in self.orders if o.get("receipt") == params.get("receipt")]}

    def _fetch(self, pid):
        return self.payments[pid]


async def make_db(*, legacy_trial_index=False, price=599):
    db = AsyncMongoMockClient()["checkout_hardening"]
    await db["billbook_plans"].insert_one({"id": "1_month", "price": price})
    await db["billbook_plans"].insert_one(dict(TRIAL_PLAN))
    subs = db["billbook_subscriptions"]
    await ensure_index(subs, "order_id", unique=True, sparse=True, name="uniq_billbook_subscription_order")
    if legacy_trial_index:  # the old shape: same keys, NOT partial
        await subs.create_index([("customer_id", 1), ("subscription_type", 1)], unique=True, name="legacy_customer_type")
    await ensure_index(subs, [("customer_id", 1), ("subscription_type", 1)], unique=True,
                       name="uniq_billbook_one_trial_per_customer", partial_filter={"subscription_type": "free_trial"})
    return db


def build(db, gateway=None, keys=True):
    gateway = gateway or Gateway()
    factory = (lambda: (gateway, "rzp_test_key", SECRET)) if keys else (lambda: (None, None, None))
    router = bs.build_billbook_subscription_router(db, lambda: None, factory)

    def ep(path, method="POST"):
        return next(r.endpoint for r in router.routes if r.path == path and method in r.methods)
    return ep, gateway


async def _second_attempt(ep, subs):
    # make the first pending record non-reusable so the second call needs a brand-new record
    await subs.update_one({"customer_id": "cust-1"}, {"$set": {"status": "cancelled"}})
    return await ep(CHECKOUT)(body(), USER)


async def records(db, cid="cust-1"):
    return [d async for d in db["billbook_subscriptions"].find({"customer_id": cid})]


def body(**over):
    return {**VALID, **over}


class CheckoutHardeningTests(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    # 1 -------------------------------------------------------------------------------------------
    def test_01_valid_paid_plan_creates_order_and_pending_record(self):
        async def go():
            db = await make_db(); ep, gw = build(db)
            res = await ep(CHECKOUT)(body(), USER)
            data = res["data"]
            self.assertEqual((data["amount"], data["currency"]), (59900, "INR"))
            self.assertEqual(data["order_id"], gw.orders[0]["id"])
            self.assertEqual(gw.orders[0]["receipt"], data["subscription_id"])
            rec = (await records(db))[0]
            self.assertEqual((rec["status"], rec["order_id"], rec["subscription_type"]), ("pending", data["order_id"], "paid"))
            self.assertIsNone(rec["expires_at"])
        self.run_async(go())

    def test_01b_299_rupee_plan_uses_server_price_and_creates_29900_paise_order(self):
        async def go():
            db = await make_db(price=299); ep, gw = build(db)
            res = await ep(CHECKOUT)(body(amount=1, price=1), USER)
            self.assertEqual(res["data"]["amount"], 29900)
            self.assertEqual(gw.orders[0]["amount"], 29900)
            rec = (await records(db))[0]
            self.assertEqual(rec["amount"], 299)
            self.assertEqual(rec["status"], "pending")
        self.run_async(go())

    # 2 / 3 ---------------------------------------------------------------------------------------
    def test_02_invalid_plan_rejected_without_side_effects(self):
        async def go():
            db = await make_db(); ep, gw = build(db)
            with self.assertRaises(HTTPException) as cm:
                await ep(CHECKOUT)(body(plan_id="not_a_plan"), USER)
            self.assertEqual(cm.exception.status_code, 422)
            self.assertEqual((len(gw.orders), len(await records(db))), (0, 0))
        self.run_async(go())

    def test_03_client_supplied_price_and_status_are_ignored(self):
        async def go():
            db = await make_db(); ep, gw = build(db)
            await ep(CHECKOUT)(body(amount=1, price=1, status="active", customer_id="someone-else", expires_at="2099-01-01"), USER)
            self.assertEqual(gw.orders[0]["amount"], 59900)
            rec = (await records(db))[0]
            self.assertEqual((rec["status"], rec["amount"], rec["customer_id"]), ("pending", 599, "cust-1"))
            self.assertEqual(await records(db, "someone-else"), [])
        self.run_async(go())

    # 4 -------------------------------------------------------------------------------------------
    def test_04_db_failure_is_controlled_logged_and_creates_no_gateway_order(self):
        async def go():
            db = await make_db()

            async def boom(col, doc):
                raise RuntimeError("mongodb://secret-host:27017 exploded")
            ep, gw = build(ProxyDB(db, lambda c: FlakyCollection(c, boom)))
            with self.assertLogs("ntaxco.billbook.checkout", level="ERROR"):
                with self.assertRaises(HTTPException) as cm:
                    await ep(CHECKOUT)(body(), USER)
            d = cm.exception.detail
            self.assertEqual((cm.exception.status_code, d["code"]), (500, "SUBSCRIPTION_PERSISTENCE_FAILED"))
            self.assertTrue(d["correlation_id"])
            self.assertNotIn("secret-host", str(d))
            self.assertEqual(len(gw.orders), 0)  # fail closed: no order without a local record
        self.run_async(go())

    def test_04b_unexpected_error_anywhere_returns_structured_500(self):
        async def go():
            db = await make_db()

            class Down(FlakyCollection):
                def find(self, *a, **k):
                    raise ConnectionError("db down")
            ep, _ = build(ProxyDB(db, lambda c: Down(c, None)))
            with self.assertLogs("ntaxco.billbook.checkout", level="ERROR"):
                with self.assertRaises(HTTPException) as cm:
                    await ep(CHECKOUT)(body(), USER)
            self.assertEqual((cm.exception.status_code, cm.exception.detail["code"]), (500, "CHECKOUT_FAILED"))
            self.assertNotIn("db down", str(cm.exception.detail))
        self.run_async(go())

    # 5 -------------------------------------------------------------------------------------------
    def test_05_legacy_non_partial_index_is_healed_and_repeat_checkouts_work(self):
        """The reproduced root cause: second paid attempt hit E11000 on (customer_id, subscription_type)."""
        async def go():
            db = await make_db(legacy_trial_index=True); ep, gw = build(db)
            first = await ep(CHECKOUT)(body(), USER)
            await ep(CANCEL)({"order_id": first["data"]["order_id"]}, USER)
            second = await ep(CHECKOUT)(body(), USER)  # used to be HTTP 500
            self.assertNotEqual(first["data"]["subscription_id"], second["data"]["subscription_id"])
            names = [i["name"] async for i in db["billbook_subscriptions"].list_indexes()]
            self.assertNotIn("legacy_customer_type", names)
            self.assertIn("uniq_billbook_one_trial_per_customer", names)
            self.assertEqual(len(await records(db)), 2)  # nothing deleted
        self.run_async(go())

    def test_05b_unrepairable_conflict_is_a_controlled_error_with_no_gateway_order(self):
        async def go():
            db = await make_db()
            subs = db["billbook_subscriptions"]
            await subs.drop_index("uniq_billbook_one_trial_per_customer")
            # An index we must NOT touch automatically (it has its own partial filter) that still rejects a 2nd paid record.
            await subs.create_index([("customer_id", 1), ("subscription_type", 1)], unique=True, name="custom_rule",
                                    partialFilterExpression={"subscription_type": {"$exists": True}})
            ep, gw = build(db)
            await ep(CHECKOUT)(body(), USER)
            with self.assertRaises(HTTPException) as cm:
                await _second_attempt(ep, subs)
            self.assertEqual(cm.exception.detail["code"], "SUBSCRIPTION_STORAGE_CONFLICT")
            self.assertEqual(len(gw.orders), 1)  # no extra order for the failed attempt
            self.assertEqual(len(await records(db)), 1)
            self.assertIn("custom_rule", [i["name"] async for i in subs.list_indexes()])  # left untouched
        self.run_async(go())

    def test_05c_repair_is_idempotent_and_never_changes_documents(self):
        async def go():
            db = AsyncMongoMockClient()["x"]; subs = db["billbook_subscriptions"]
            await subs.create_index([("customer_id", 1), ("subscription_type", 1)], unique=True, name="legacy")
            await subs.insert_one({"id": "t0", "customer_id": "c", "subscription_type": "free_trial"})
            await subs.insert_one({"id": "p0", "customer_id": "c", "subscription_type": "paid"})
            self.assertEqual(await repair_legacy_trial_index(subs), "repaired")
            self.assertEqual(await repair_legacy_trial_index(subs), "already_correct")
            self.assertEqual(await subs.count_documents({}), 2)
            await subs.insert_one({"id": "p1", "customer_id": "c", "subscription_type": "paid"})  # now allowed
            with self.assertRaises(DuplicateKeyError):  # one-trial-per-customer rule still enforced
                await subs.insert_one({"id": "t1", "customer_id": "c", "subscription_type": "free_trial"})
        self.run_async(go())

    # 6 -------------------------------------------------------------------------------------------
    def test_06_razorpay_failure_activates_nothing_and_marks_attempt_failed(self):
        async def go():
            db = await make_db(); ep, gw = build(db, Gateway(fail_create=True))
            with self.assertRaises(HTTPException) as cm:
                await ep(CHECKOUT)(body(), USER)
            self.assertEqual((cm.exception.status_code, cm.exception.detail["code"]), (502, "PAYMENT_GATEWAY_ERROR"))
            recs = await records(db)
            self.assertEqual([r["status"] for r in recs], ["failed"])
            # a later retry is a legitimate new checkout (failed attempts are not blockers)
            gw.fail_create = False
            res = await ep(CHECKOUT)(body(), USER)
            self.assertTrue(res["success"])
            self.assertEqual(sorted(r["status"] for r in await records(db)), ["failed", "pending"])
        self.run_async(go())

    # 7 -------------------------------------------------------------------------------------------
    def test_07_order_created_then_save_fails_is_recoverable_without_a_second_order(self):
        async def go():
            db = await make_db()
            state = {"fail": True}

            class Flaky(FlakyCollection):
                async def update_one(self, q, u, *a, **k):
                    if state["fail"] and "order_id" in (u.get("$set") or {}):
                        raise RuntimeError("write failed")
                    return await self._col.update_one(q, u, *a, **k)
                async def insert_one(self, doc):
                    return await self._col.insert_one(doc)
            ep, gw = build(ProxyDB(db, lambda c: Flaky(c, None)))
            with self.assertLogs("ntaxco.billbook.checkout", level="ERROR") as logs:
                with self.assertRaises(HTTPException) as cm:
                    await ep(CHECKOUT)(body(), USER)
            self.assertEqual(cm.exception.detail["code"], "SUBSCRIPTION_PERSISTENCE_FAILED")
            self.assertTrue(any("order_1" in line and "attach_order_failed" in line for line in logs.output))  # reference kept
            self.assertEqual([r["status"] for r in await records(db)], ["pending"])  # never active
            state["fail"] = False
            res = await ep(CHECKOUT)(body(), USER)  # retry reconciles by receipt
            self.assertEqual(res["data"]["order_id"], "order_1")
            self.assertEqual(len(gw.orders), 1)
            self.assertEqual(len(await records(db)), 1)
        self.run_async(go())

    # 8 -------------------------------------------------------------------------------------------
    def test_08_repeated_and_concurrent_requests_do_not_duplicate(self):
        async def go():
            db = await make_db(); ep, gw = build(db)
            results = await asyncio.gather(*[ep(CHECKOUT)(body(), USER) for _ in range(6)])
            self.assertEqual(len({r["data"]["order_id"] for r in results}), 1)
            self.assertEqual((len(gw.orders), len(await records(db))), (1, 1))
            other = await ep(CHECKOUT)(body(), OTHER)  # other customers are never blocked
            self.assertNotEqual(other["data"]["order_id"], results[0]["data"]["order_id"])
        self.run_async(go())

    # 9 -------------------------------------------------------------------------------------------
    def _pay(self, gw, order_id, status="captured", pid="pay_1", amount=59900):
        gw.payments[pid] = {"id": pid, "order_id": order_id, "status": status, "amount": amount, "currency": "INR"}
        return {"razorpay_order_id": order_id, "razorpay_payment_id": pid, "razorpay_signature": sign(order_id, pid)}

    def test_09_valid_signature_activates_once_and_repeat_verify_is_idempotent(self):
        async def go():
            db = await make_db(); ep, gw = build(db)
            order = (await ep(CHECKOUT)(body(), USER))["data"]["order_id"]
            self.assertEqual((await records(db))[0]["status"], "pending")  # not active before payment
            payload = self._pay(gw, order)
            first = await ep(VERIFY)(payload, USER)
            again = await ep(VERIFY)(payload, USER)
            self.assertEqual((first["data"]["state"], again["data"]["state"]), ("active", "active"))
            rec = (await records(db))[0]
            self.assertEqual((rec["status"], rec["payment_id"]), ("active", "pay_1"))
            self.assertEqual(await db["erp_payments"].count_documents({}), 1)
            self.assertEqual(len(await records(db)), 1)
        self.run_async(go())

    def test_10_invalid_signature_does_not_activate(self):
        async def go():
            db = await make_db(); ep, gw = build(db)
            order = (await ep(CHECKOUT)(body(), USER))["data"]["order_id"]
            payload = {**self._pay(gw, order), "razorpay_signature": "0" * 64}
            with self.assertRaises(HTTPException) as cm:
                await ep(VERIFY)(payload, USER)
            self.assertEqual(cm.exception.status_code, 400)
            self.assertEqual((await records(db))[0]["status"], "pending")
        self.run_async(go())

    def test_11_failed_or_cancelled_payment_never_activates(self):
        async def go():
            db = await make_db(); ep, gw = build(db)
            order = (await ep(CHECKOUT)(body(), USER))["data"]["order_id"]
            res = await ep(VERIFY)(self._pay(gw, order, status="failed"), USER)
            self.assertEqual(res["data"]["state"], "failed")
            order2 = (await ep(CHECKOUT)(body(), USER))["data"]["order_id"]
            cancelled = await ep(CANCEL)({"order_id": order2}, USER)
            self.assertEqual(cancelled["data"]["state"], "cancelled")
            self.assertNotIn("active", [r["status"] for r in await records(db)])
        self.run_async(go())

    def test_11b_amount_mismatch_is_rejected(self):
        async def go():
            db = await make_db(); ep, gw = build(db)
            order = (await ep(CHECKOUT)(body(), USER))["data"]["order_id"]
            with self.assertRaises(HTTPException):
                await ep(VERIFY)(self._pay(gw, order, amount=100), USER)
            self.assertEqual((await records(db))[0]["status"], "pending")
        self.run_async(go())

    # 13 / 14 -------------------------------------------------------------------------------------
    def test_13_missing_razorpay_configuration_is_a_clear_503_and_writes_nothing(self):
        async def go():
            db = await make_db(); ep, _ = build(db, keys=False)
            with self.assertRaises(HTTPException) as cm:
                await ep(CHECKOUT)(body(), USER)
            self.assertEqual(cm.exception.status_code, 503)
            self.assertIn("RAZORPAY", str(cm.exception.detail).upper())
            self.assertEqual(len(await records(db)), 0)
        self.run_async(go())

    def test_14_free_trial_still_works_alongside_paid_checkout(self):
        async def go():
            db = await make_db(legacy_trial_index=True); ep, gw = build(db)
            await ep(CHECKOUT)(body(), USER)
            trial = await ep("/api/billbook/trials/activate")({"plan_id": "trial_1"}, OTHER)
            self.assertTrue(trial["data"]["trial"])
            again = await ep("/api/billbook/trials/activate")({"plan_id": "trial_1"}, OTHER)
            self.assertTrue(again["data"]["resumed"])
        self.run_async(go())

    def test_15_signed_in_checkout_does_not_disturb_guest_checkout(self):
        async def go():
            db = await make_db(); ep, gw = build(db)
            guest = await ep("/api/billbook/guest-checkout")(body())
            signed = await ep(CHECKOUT)(body(), USER)
            self.assertNotEqual(guest["data"]["order_id"], signed["data"]["order_id"])
            self.assertEqual(len(gw.orders), 2)
        self.run_async(go())

    def test_16_secrets_and_card_data_never_logged(self):
        async def go():
            db = await make_db(); ep, _ = build(db)
            with self.assertLogs("ntaxco.billbook.checkout", level="INFO") as logs:
                await ep(CHECKOUT)(body(), USER)
            text = "\n".join(logs.output)
            self.assertNotIn(SECRET, text)
            self.assertNotIn("9876543210", text)
            self.assertNotIn("asha@example.com", text)
        self.run_async(go())


if __name__ == "__main__":
    unittest.main()
