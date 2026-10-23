// Partner login gate helpers + wiring. Run: node --test scripts/partner-share.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { safePartnerNextPath, partnerLoginUrl, partnerErrorMessage } from "../src/lib/partnerShare.js";

const src = (p) => readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../src", p), "utf8");
const TOKEN = "A".repeat(43);

test("post-login return path accepts only partner invite links", () => {
  assert.equal(safePartnerNextPath(`/partner/booking/${TOKEN}`), `/partner/booking/${TOKEN}`);
  for (const bad of ["https://evil.test", "//evil.test", "/customer/dashboard", "/partner/booking/short", `/partner/booking/${TOKEN}/x`,
    `/partner/booking/${TOKEN}?x=1`, `/partner/booking/../${TOKEN}`, "javascript:alert(1)", "", null, undefined]) {
    assert.equal(safePartnerNextPath(bad), null, String(bad));
  }
});

test("login URLs round-trip the invite path", () => {
  const path = `/partner/booking/${TOKEN}`;
  assert.equal(new URL(partnerLoginUrl(path), "http://x").searchParams.get("next"), path);
  const signup = new URL(partnerLoginUrl(path, "signup"), "http://x");
  assert.equal(signup.searchParams.get("mode"), "signup");
  assert.equal(safePartnerNextPath(signup.searchParams.get("next")), path);
});

test("error messages", () => {
  const e = (status, detail) => ({ response: { status, data: { detail } } });
  assert.match(partnerErrorMessage(e(410)), /expired/);
  assert.match(partnerErrorMessage(e(404)), /not valid/);
  assert.equal(partnerErrorMessage(e(403, "already used")), "already used");
  assert.match(partnerErrorMessage(e(500)), /try again/);
});

test("routes require login and the old public booking read is gone", () => {
  const app = src("App.js");
  assert.match(app, /path="\/partner\/booking\/:token" element=\{<PartnerBookingView \/>\}/);
  assert.match(app, /path="shared-bookings" element=\{<Protected><CustomerSharedBookings \/><\/Protected>\}/);
  assert.match(app, /path="shared-bookings\/:accessId" element=\{<Protected><SharedBookingDetail \/><\/Protected>\}/);
  const view = src("pages/customer/PartnerBookingView.jsx");
  assert.ok(view.includes("/partner/shares/redeem"));
  assert.ok(!/booking_no|data\.service/.test(view), "gate page never renders booking data");
  assert.ok(!/localStorage/.test(view + src("pages/customer/CustomerSharedBookings.jsx")));
});

// ---- Shared Bookings dashboard -------------------------------------------------------------------------------
import {
  expiryLabel, isSafeDocumentDataUri, safeDownloadName, formatShareDate, LINK_EXPIRY_OPTIONS, ACCESS_PERIOD_OPTIONS,
} from "../src/lib/partnerShare.js";

test("expiry labels", () => {
  const now = Date.parse("2026-10-23T00:00:00Z");
  assert.equal(expiryLabel("2026-10-22T00:00:00Z", now), "expired");
  assert.equal(expiryLabel("2026-10-23T05:00:00Z", now), "expires in 5 hours");
  assert.equal(expiryLabel("2026-10-23T00:30:00Z", now), "expires in 1 hour");
  assert.equal(expiryLabel("2026-10-26T00:00:00Z", now), "expires in 3 days");
  assert.equal(expiryLabel(null, now), "");
  assert.equal(expiryLabel("not-a-date", now), "");
  assert.equal(formatShareDate("nope"), "");
});

test("expiry choices never exceed what the backend accepts", () => {
  assert.ok(LINK_EXPIRY_OPTIONS.every((o) => o.days >= 1 && o.days <= 90));
  assert.ok(ACCESS_PERIOD_OPTIONS.every((o) => o.days === "" || (o.days >= 1 && o.days <= 365)));
});

test("only safe document data URIs can be downloaded", () => {
  assert.equal(isSafeDocumentDataUri("data:application/pdf;base64,JVBERi0="), true);
  assert.equal(isSafeDocumentDataUri("data:image/png;base64,iVBORw0="), true);
  for (const bad of ["data:text/html;base64,PHNjcmlwdD4=", "data:image/svg+xml;base64,PHN2Zz4=", "javascript:alert(1)",
    "https://evil.test/a.pdf", "", null, undefined, 42]) {
    assert.equal(isSafeDocumentDataUri(bad), false, String(bad));
  }
  assert.equal(safeDownloadName("../../etc/passwd"), "_.._etc_passwd");
  assert.equal(safeDownloadName("   "), "document");
  assert.equal(safeDownloadName("GST return.pdf"), "GST return.pdf");
});

test("dashboard, page and nav are wired to the Shared Bookings feature", () => {
  assert.match(src("pages/customer/CustomerDashboard.jsx"), /<SharedBookingsPanel limit=\{4\} showViewAll/);
  const panel = src("components/shared/SharedBookingsPanel.jsx");
  for (const call of ["/partner/shared-bookings", "/partner/my-shares", "partner-share`, { regenerate: true", "partner-access/${"]) {
    assert.ok(panel.includes(call), call);
  }
  assert.match(src("components/layout/CustomerLayout.jsx"), /Shared Bookings/);
  const page = src("pages/customer/CustomerSharedBookings.jsx");
  assert.ok(page.includes("/documents/"), "documents download through the authorized partner endpoint");
  assert.ok(!/(localStorage|sessionStorage)\s*\./.test(page + panel + src("components/shared/PartnerSharePanel.jsx")), "no browser storage calls");
});
