# Partner and sub-partner data isolation

## Authorization model

- `erp_booking_partner_shares` stores one-use invitation links. A delegated link records `parent_access_id`, `delegated_by`, `root_share_id`, and `hierarchy_depth`.
- `erp_booking_partner_access` stores the grant for the specific authenticated partner account. Delegated grants store their `parent_access_id`, `delegated_by`, `root_share_id`, and `hierarchy_depth`.
- Every partner read requires an active grant whose `partner_user_id` matches the authenticated account. Delegated grants also require every ancestor grant to remain active.
- Effective permissions are intersected with every ancestor grant. Descendants cannot gain document or update permissions beyond what the original customer granted, and later permission reductions propagate to descendants.
- Delegated links expire no later than the parent grant. Revoking an ancestor invalidates all descendants on their next request, including public status and redemption checks.
- A partner may create a delegated link only from a grant held by their own account and may revoke only a link created by their own account from that grant.
- Partner APIs return a safe booking projection. They do not grant access to normal customer bookings, invoices, payments, customer details, or private documents.

## Endpoints

- `POST /api/partner/shared-bookings/{access_id}/share` — create a sub-partner invite; optional `partner_email`, `show_updates`, and `document_ids` must stay within inherited permissions.
- `DELETE /api/partner/shared-bookings/{access_id}/share/{share_id}` — revoke an invite created by the current partner.
- Existing owner sharing, access revocation, link expiry, and redeem endpoints remain in place.

## Automated coverage

`backend/tests/test_partner_shared_dashboard.py` includes multi-level delegation, account isolation, original-customer permission ceilings, ancestor revocation, and expired-parent-link cases. Run in an environment with `backend/requirements-dev.txt` installed:

```bash
cd backend
python -m pytest -c /dev/null -q tests/test_partner_shared_dashboard.py
```

The development environment used to prepare this archive did not have `mongomock-motor` or `pytest-xdist` installed and had no package-index network access, so runtime pytest execution was unavailable here. Python syntax compilation was performed successfully.


## Shared Bookings management dashboard additions

- Owner-created and delegated invitations are email-bound; the customer UI requires a valid email before creating an invitation and redemption compares normalized account email.
- `GET /api/partner/my-shares` now includes `invitations[]` with invitation state (Pending/Claimed/Expired/Revoked), registration state, partner identity when available, claim time, account creation time when available, last activity when available, and expiry.
- `DELETE /api/bookings/{booking_id}/partner-invitations/{share_id}` lets only the booking owner revoke an invite for their own booking.
- Owner `PATCH /api/bookings/{booking_id}/partner-access/{access_id}` accepts `active: false|true` to deactivate or restore access. Status changes are timestamped and appended to `erp_partner_share_audit`.
- `PATCH /api/partner/shared-bookings/{access_id}/my-access` accepts `action: deactivate|restore|leave` and is scoped to the authenticated partner's own grant only.
- Access state is distinct from invitation and registration state. Revoked, inactive, and expired grants fail closed in authorization checks. No passwords or auth tokens are returned by these endpoints.
