"""Idempotent MongoDB index initialization helpers for NTAXCO.

Indexes are never dropped or renamed here. If an equivalent index already exists
under a different name, it is reused. If the same key pattern exists with
incompatible options, the helper reports it and leaves the existing index alone.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)


def _keys(value: Any) -> tuple[tuple[str, int], ...]:
    if isinstance(value, str):
        return ((value, 1),)
    if isinstance(value, dict):
        return tuple((str(k), int(v)) for k, v in value.items())
    return tuple((str(k), int(v)) for k, v in value)


def _option(index: dict, name: str, default: Any = None) -> Any:
    return index.get(name, default)


def _compatible(index: dict, keys: tuple[tuple[str, int], ...], unique: bool,
                sparse: bool, partial_filter: dict | None) -> bool:
    return (
        _keys(index.get("key", {})) == keys
        and bool(_option(index, "unique", False)) == bool(unique)
        and bool(_option(index, "sparse", False)) == bool(sparse)
        and _option(index, "partialFilterExpression") == partial_filter
    )


async def ensure_index(collection, keys: Any, *, unique: bool = False,
                       sparse: bool = False, name: str | None = None,
                       partial_filter: dict | None = None,
                       logger_: logging.Logger | None = None) -> bool:
    """Ensure an index exists without creating equivalent duplicate indexes.

    Returns True when a compatible index already existed or was created.
    Returns False when an incompatible same-key index exists or a unique index
    cannot safely be created because duplicate/invalid values are present.
    """
    log = logger_ or logger
    normalized = _keys(keys)

    existing = await collection.list_indexes().to_list(length=None)
    for index in existing:
        if _compatible(index, normalized, unique, sparse, partial_filter):
            log.info(
                "Reusing compatible MongoDB index %s on %s",
                index.get("name"), getattr(collection, "name", "<collection>"),
            )
            return True

    same_keys = [idx for idx in existing if _keys(idx.get("key", {})) == normalized]
    if same_keys:
        log.warning(
            "MongoDB index conflict on %s for keys %s: existing index(es) "
            "have incompatible options: %s. No existing index will be "
            "dropped or renamed.",
            getattr(collection, "name", "<collection>"),
            normalized,
            [idx.get("name") for idx in same_keys],
        )
        return False

    if unique and partial_filter is None:
        # The BillBook plan migration is a full unique index on `id`. Check
        # data before asking MongoDB to enforce uniqueness so legacy bad data
        # is reported clearly and startup never needs to delete/repair rows.
        invalid: list[Any] = []
        counts: dict[Any, int] = {}
        cursor = collection.find({}, {"id": 1, "_id": 0})
        async for doc in cursor:
            value = doc.get("id")
            if value is None or value == "":
                invalid.append(value)
            else:
                try:
                    counts[value] = counts.get(value, 0) + 1
                except TypeError:
                    invalid.append(value)
        duplicates = [value for value, count in counts.items() if count > 1]
        if invalid or duplicates:
            log.error(
                "Cannot create unique MongoDB index %s on %s: invalid/missing "
                "id values=%d, duplicate id values=%d. Data was not modified "
                "and no index was dropped or renamed.",
                name or "<unnamed>",
                getattr(collection, "name", "<collection>"),
                len(invalid), len(duplicates),
            )
            return False

    kwargs = {"unique": unique, "sparse": sparse}
    if name:
        kwargs["name"] = name
    if partial_filter is not None:
        kwargs["partialFilterExpression"] = partial_filter
    try:
        await collection.create_index(keys, **kwargs)
    except Exception as exc:
        log.error(
            "MongoDB index creation failed for %s on %s: %s. "
            "No existing index was dropped or renamed.",
            name or "<unnamed>", getattr(collection, "name", "<collection>"), exc,
        )
        return False
    log.info("Created MongoDB index %s on %s", name or "<unnamed>",
             getattr(collection, "name", "<collection>"))
    return True


async def repair_legacy_unique_index(collection, field: str, *, name: str,
                                     logger_: logging.Logger | None = None) -> str:
    """Replace a legacy NON-sparse unique index on ``field`` with a sparse unique one.

    Why: a unique index that is not sparse indexes a missing field as ``null``, so only ONE
    document without the field can ever exist. For ``billbook_subscriptions.order_id`` that means
    the first free trial (which has no Razorpay order) is accepted and every later trial fails
    with a duplicate-key error on ``order_id: null``.

    Safety rules:
      * Only acts on an index with exactly this key, unique, NOT sparse, no partial filter.
      * Refuses (changes nothing) if two documents share the same real, non-null value.
      * No document is modified or deleted. If re-creating fails, the legacy index is restored.

    Returns one of: ``"absent"``, ``"already_correct"``, ``"repaired"``, ``"skipped_duplicates"``,
    ``"failed"``.
    """
    log = logger_ or logger
    wanted = ((field, 1),)
    try:
        existing = await collection.list_indexes().to_list(length=None)
    except Exception as exc:  # diagnostic aid only
        log.error("Cannot list indexes on %s (error_type=%s)", getattr(collection, "name", "?"), type(exc).__name__)
        return "failed"
    same = [i for i in existing if _keys(i.get("key", {})) == wanted]
    if not same:
        return "absent"
    legacy = [i for i in same if i.get("unique") and not i.get("sparse") and not i.get("partialFilterExpression")]
    if not legacy:
        return "already_correct"
    old = legacy[0]

    # Real, non-null duplicates would make the new unique index impossible. Never auto-delete them.
    dupes = await collection.aggregate([
        {"$match": {field: {"$type": "string"}}},
        {"$group": {"_id": f"${field}", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
        {"$limit": 1},
    ]).to_list(length=1)
    if dupes:
        log.error("Legacy unique index %s on %s left unchanged: duplicate real %s values exist; "
                  "resolve them manually first.", old.get("name"), getattr(collection, "name", "?"), field)
        return "skipped_duplicates"

    try:
        await collection.drop_index(old["name"])
    except Exception as exc:
        log.error("Could not drop legacy index %s (error_type=%s)", old.get("name"), type(exc).__name__)
        return "failed"
    try:
        await collection.create_index([(field, 1)], unique=True, sparse=True, name=name)
    except Exception as exc:
        log.error("Creating sparse unique index %s failed (%s); restoring legacy index.", name, exc)
        try:
            await collection.create_index([(field, 1)], unique=True, name=old["name"])
        except Exception as restore_exc:  # pragma: no cover - needs operator attention
            log.critical("Could not restore legacy index %s (error_type=%s)", old.get("name"), type(restore_exc).__name__)
        return "failed"
    log.warning("Replaced legacy non-sparse unique index %s on %s.%s with sparse unique index %s.",
                old.get("name"), getattr(collection, "name", "?"), field, name)
    return "repaired"


async def repair_legacy_trial_index(collection, *, name: str = "uniq_billbook_one_trial_per_customer",
                                    logger_: logging.Logger | None = None) -> str:
    """Replace a legacy NON-partial unique ``(customer_id, subscription_type)`` index with the partial one.

    Why: the business rule is "one free trial per customer", enforced by a unique index that must
    only cover ``subscription_type == "free_trial"`` documents. If an older, non-partial unique index
    with the same keys exists, every paid checkout (which has no / a different ``subscription_type``)
    collides after the customer's first record: ``E11000 ... subscription_type: null``. The startup
    ``ensure_index`` deliberately leaves such an index alone, so the conflict was silent.

    Safety rules (same philosophy as ``repair_legacy_unique_index``):
      * Acts only on a unique index with exactly these keys and NO partialFilterExpression.
      * Refuses if two ``free_trial`` documents already share a customer_id (the new index could not be built).
      * Never modifies or deletes documents; if re-creation fails the legacy index is restored.

    Returns: ``"absent"``, ``"already_correct"``, ``"repaired"``, ``"skipped_duplicates"`` or ``"failed"``.
    """
    log = logger_ or logger
    wanted = (("customer_id", 1), ("subscription_type", 1))
    try:
        existing = await collection.list_indexes().to_list(length=None)
    except Exception as exc:
        log.error("Cannot list indexes on %s (error_type=%s)", getattr(collection, "name", "?"), type(exc).__name__)
        return "failed"
    same = [i for i in existing if _keys(i.get("key", {})) == wanted]
    if not same:
        return "absent"
    legacy = [i for i in same if i.get("unique") and not i.get("partialFilterExpression")]
    if not legacy:
        return "already_correct"
    old = legacy[0]
    partial = {"subscription_type": "free_trial"}
    try:
        dupes = await collection.aggregate([
            {"$match": {"subscription_type": "free_trial"}},
            {"$group": {"_id": "$customer_id", "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}},
            {"$limit": 1},
        ]).to_list(length=1)
    except Exception as exc:
        log.error("Cannot check free_trial duplicates on %s (error_type=%s)", getattr(collection, "name", "?"), type(exc).__name__)
        return "failed"
    if dupes:
        log.error("Legacy unique index %s on %s left unchanged: a customer already has several free_trial "
                  "records; resolve them manually first.", old.get("name"), getattr(collection, "name", "?"))
        return "skipped_duplicates"
    try:
        await collection.drop_index(old["name"])
    except Exception as exc:
        log.error("Could not drop legacy index %s (error_type=%s)", old.get("name"), type(exc).__name__)
        return "failed"
    try:
        await collection.create_index([("customer_id", 1), ("subscription_type", 1)], unique=True,
                                      name=name, partialFilterExpression=partial)
    except Exception as exc:
        log.error("Creating partial unique index %s failed (error_type=%s); restoring legacy index.", name, type(exc).__name__)
        try:
            await collection.create_index([("customer_id", 1), ("subscription_type", 1)], unique=True, name=old["name"])
        except Exception as restore_exc:  # pragma: no cover - needs operator attention
            log.critical("Could not restore legacy index %s (error_type=%s)", old.get("name"), type(restore_exc).__name__)
        return "failed"
    log.warning("Replaced legacy non-partial unique index %s on %s with partial unique index %s.",
                old.get("name"), getattr(collection, "name", "?"), name)
    return "repaired"


async def repair_legacy_payment_id_index(collection, *, name="bb_sub_payment_once", logger_=None):
    """Replace a legacy full unique payment-id index with a partial unique index.

    Missing/null payment IDs are valid for free trials. Uniqueness is retained
    for real string Razorpay IDs. Never changes/deletes documents; duplicate
    real IDs stop the migration, and a failed replacement attempts restoration.
    """
    log = logger_ or logger
    field = "razorpay_payment_id"
    wanted = ((field, 1),)
    partial = {field: {"$type": "string"}}
    try:
        indexes = await collection.list_indexes().to_list(length=None)
    except Exception as exc:
        log.error("Cannot list payment-id indexes (error_type=%s)", type(exc).__name__)
        return "failed"
    same = [idx for idx in indexes if _keys(idx.get("key", {})) == wanted]
    if not same:
        return "absent"
    if any(idx.get("unique") and idx.get("partialFilterExpression") == partial for idx in same):
        return "already_correct"
    legacy = [idx for idx in same if idx.get("unique") and not idx.get("sparse") and not idx.get("partialFilterExpression")]
    if not legacy:
        log.warning("Unexpected payment-id index options; leaving existing index unchanged")
        return "failed"
    old = legacy[0]
    try:
        dupes = await collection.aggregate([
            {"$match": {field: {"$type": "string"}}},
            {"$group": {"_id": f"${field}", "n": {"$sum": 1}}},
            {"$match": {"n": {"$gt": 1}}}, {"$limit": 1}
        ]).to_list(length=1)
    except Exception as exc:
        log.error("Cannot check duplicate payment IDs (error_type=%s)", type(exc).__name__)
        return "failed"
    if dupes:
        log.error("Duplicate real payment IDs found; leaving legacy index unchanged")
        return "skipped_duplicates"
    try:
        await collection.drop_index(old["name"])
    except Exception as exc:
        log.error("Could not drop legacy payment-id index (error_type=%s)", type(exc).__name__)
        return "failed"
    try:
        await collection.create_index([(field, 1)], unique=True, name=name, partialFilterExpression=partial)
    except Exception as exc:
        log.error("Partial payment-id index creation failed; attempting to restore legacy index (error_type=%s)", type(exc).__name__)
        try:
            await collection.create_index([(field, 1)], unique=True, name=old["name"])
        except Exception as restore_exc:
            log.critical("Could not restore legacy payment-id index (error_type=%s)", type(restore_exc).__name__)
        return "failed"
    log.warning("Migrated legacy payment-id index %s to partial unique index %s", old.get("name"), name)
    return "repaired"
