import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { CalendarCheck, ChevronRight, Copy, FileText, Loader2, RefreshCw, Settings2, ShieldOff, UserMinus, Users, Share2 } from "lucide-react";
import api, { describeApiError } from "@/lib/api";
import { expiryLabel, formatShareDate } from "@/lib/partnerShare";
import { StatusBadge } from "@/components/shared/StatusBadge";
import PartnerSharePanel from "@/components/shared/PartnerSharePanel";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

// "Shared Bookings" section of the signed-in customer dashboard.
//   Shared with me  - bookings other customers shared with THIS account (GET /partner/shared-bookings)
//   Shared by me    - bookings I shared: link, expiry, partners; revoke / regenerate (GET /partner/my-shares)
// Every rule is enforced by the FastAPI backend; this component only renders what the backend returns.

function Spinner() {
  return <div className="py-8 flex justify-center text-zinc-400"><Loader2 className="h-5 w-5 animate-spin" /></div>;
}

function Empty({ text, testId }) {
  return <p className="text-sm text-muted-foreground py-6 text-center" data-testid={testId}>{text}</p>;
}

async function copyText(text) {
  if (navigator.clipboard?.writeText && window.isSecureContext) return navigator.clipboard.writeText(text);
  const el = document.createElement("textarea");
  el.value = text; el.setAttribute("readonly", ""); el.style.position = "fixed"; el.style.opacity = "0";
  document.body.appendChild(el); el.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(el);
  if (!ok) throw new Error("copy failed");
  return undefined;
}

function SharedWithMe({ rows, limit }) {
  const navigate = useNavigate();
  const shown = limit ? rows.slice(0, limit) : rows;
  if (!rows.length) return <Empty testId="shared-with-me-empty" text="Nothing is shared with you yet. When a customer sends you a booking link and you open it while signed in, it appears here." />;
  return (
    <div className="space-y-2" data-testid="shared-with-me-list">
      {shown.map((b) => (
        <div key={b.access_id} className="rounded-xl border border-zinc-200 bg-white p-3 sm:p-4" data-testid="shared-booking-row">
          <button type="button" onClick={() => navigate(`/customer/shared-bookings/${b.access_id}`)}
            className="w-full text-left flex items-center gap-3">
            <div className="h-10 w-10 shrink-0 rounded-lg bg-brand-faint flex items-center justify-center"><CalendarCheck className="h-5 w-5 text-brand-hover" /></div>
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-zinc-900 truncate">{b.service || "Service"}</p>
              <p className="text-xs text-muted-foreground truncate">
                {b.booking_no}{b.shared_by ? ` · shared by ${b.shared_by}` : ""}{b.booking_date ? ` · ${formatShareDate(b.booking_date)}` : ""}
              </p>
              <p className="text-[11px] text-muted-foreground mt-0.5 flex flex-wrap gap-x-3">
                {b.documents_count > 0 && <span className="inline-flex items-center gap-1"><FileText className="h-3 w-3" />{b.documents_count} document{b.documents_count === 1 ? "" : "s"}</span>}
                {b.access_expires_at && <span>Access {expiryLabel(b.access_expires_at)}</span>}
              </p>
            </div>
            {b.status && <StatusBadge value={b.status} />}
            <ChevronRight className="h-4 w-4 shrink-0 text-zinc-400" />
          </button>
          <div className="mt-3 flex flex-wrap justify-end gap-2">
            <button type="button" className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium hover:bg-zinc-50"
              data-testid="shared-booking-self-deactivate" onClick={async () => {
                const action = window.confirm("Permanently leave this shared booking? You will need a new invitation to regain access.") ? "leave" : "deactivate";
                try {
                  await api.patch(`/partner/shared-bookings/${b.access_id}/my-access`, { action });
                  toast.success(action === "leave" ? "You left this shared booking." : "Your access is deactivated.");
                  window.location.reload();
                } catch (error) { toast.error(describeApiError(error, "Could not update your access.")); }
              }}>Deactivate / Leave access</button>
            <button type="button" className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium hover:bg-zinc-50"
              data-testid="shared-booking-delegate" onClick={async (event) => {
                event.stopPropagation();
                const email = window.prompt("Enter the authorized sub-partner's email address:");
                if (!email?.trim()) return;
                try {
                  const { data } = await api.post(`/partner/shared-bookings/${b.access_id}/share`, {
                    partner_email: email.trim(), show_updates: !!b.can_view_updates, document_ids: [],
                  });
                  const share = data?.data;
                  const url = share?.share_url || (share ? `${window.location.origin}${share.link_path}` : "");
                  if (url) { try { await copyText(url); } catch { /* manual copy fallback */ } }
                  toast.success(url ? "Sub-partner link created and copied." : "Sub-partner link created.");
                } catch (error) { toast.error(describeApiError(error, "Could not create a sub-partner link.")); }
              }}><Share2 className="h-3.5 w-3.5" />Share with sub-partner</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function SharedByMe({ rows, limit, onChanged }) {
  const [busy, setBusy] = useState("");
  const [confirm, setConfirm] = useState(null); // { kind: "all" | "partner", row, partner }
  const [manage, setManage] = useState(null);
  const shown = limit ? rows.slice(0, limit) : rows;

  const run = async (key, fn, okMsg, failMsg) => {
    setBusy(key);
    try { await fn(); toast.success(okMsg); await onChanged(); } catch (e) { toast.error(describeApiError(e, failMsg)); } finally { setBusy(""); }
  };

  const regenerate = (row) => run(`regen-${row.booking_id}`,
    async () => {
      const { data } = await api.post(`/bookings/${row.booking_id}/partner-share`, { regenerate: true, ...(row.link?.partner_email ? { partner_email: row.link.partner_email } : {}) });
      const share = data?.data;
      const url = share?.share_url || (share ? `${window.location.origin}${share.link_path}` : "");
      if (url) { try { await copyText(url); } catch { /* copy is a convenience only */ } }
    },
    "New link created and copied. The previous link no longer works.", "Could not regenerate the link.");

  const copyLink = async (row) => {
    const url = row.link?.share_url || (row.link ? `${window.location.origin}${row.link.link_path}` : "");
    if (!url) return;
    try { await copyText(url); toast.success("Link copied."); } catch { toast.error("Could not copy automatically."); }
  };

  const doConfirm = async () => {
    const c = confirm;
    setConfirm(null);
    if (!c) return;
    if (c.kind === "all") {
      await run(`all-${c.row.booking_id}`, () => api.delete(`/bookings/${c.row.booking_id}/partner-share`),
        "Sharing stopped. The link no longer works and every partner lost access.", "Could not stop sharing.");
    } else {
      await run(`p-${c.partner.access_id}`, () => api.delete(`/bookings/${c.row.booking_id}/partner-access/${c.partner.access_id}`),
        "Partner access removed.", "Could not remove this partner.");
    }
  };

  if (!rows.length) return <Empty testId="shared-by-me-empty" text="You have not shared any booking yet. Open one of your bookings and choose “Share with Partner”." />;

  return (
    <>
      <div className="space-y-3" data-testid="shared-by-me-list">
        {shown.map((row) => (
          <div key={row.booking_id} className="rounded-xl border border-zinc-200 bg-white p-3 sm:p-4" data-testid="shared-by-me-row">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 shrink-0 rounded-lg bg-brand-faint flex items-center justify-center"><Users className="h-5 w-5 text-brand-hover" /></div>
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-zinc-900 truncate">{row.service || "Service"}</p>
                <p className="text-xs text-muted-foreground truncate">{row.booking_no}{row.booking_date ? ` · ${formatShareDate(row.booking_date)}` : ""}</p>
              </div>
              {row.status && <StatusBadge value={row.status} />}
            </div>

            <div className="mt-3 text-xs text-zinc-700 space-y-1.5">
              {row.link ? (
                <p data-testid="shared-link-state">Invite link active · <span className="text-muted-foreground">{expiryLabel(row.link.expires_at)}</span>
                  {row.link.partner_email ? <span className="text-muted-foreground"> · for {row.link.partner_email}</span> : null}</p>
              ) : <p className="text-muted-foreground" data-testid="shared-link-state">No unused invite link (the last one was used or has expired).</p>}
              {row.invitations?.length > 0 && (
                <div className="rounded-lg border border-zinc-100 bg-zinc-50 p-2.5 space-y-2" data-testid="shared-invitations-list">
                  <p className="font-semibold text-zinc-800">Invitations and registration</p>
                  {row.invitations.map((invite) => (
                    <div key={invite.invite_id} className="border-b border-zinc-200 last:border-0 pb-2 last:pb-0">
                      <p className="font-medium break-all">{invite.partner_name ? `${invite.partner_name} · ` : ""}{invite.partner_email || "Email not recorded"}</p>
                      <p className="text-muted-foreground">Invitation: {invite.invitation_status} · Account: {invite.registration_status} · Access: {invite.access_status || "Pending"}</p>
                      <p className="text-muted-foreground">Invited {formatShareDate(invite.created_at) || "date unavailable"} · {invite.invitation_claimed_at ? `Claimed ${formatShareDate(invite.invitation_claimed_at)}` : "Not claimed"} · Account created {formatShareDate(invite.account_created_at) || "unavailable"}</p>
                      <p className="text-muted-foreground">Last activity {formatShareDate(invite.last_activity_at) || "unavailable"} · Access expiry {formatShareDate(invite.access_expires_at) || "not set"}</p>
                      {invite.invitation_status === "Pending" && <button type="button" className="underline text-blue-700" disabled={!!busy} onClick={() => run(`invite-revoke-${invite.invite_id}`, () => api.delete(`/bookings/${row.booking_id}/partner-invitations/${invite.invite_id}`), "Invitation revoked.", "Could not revoke invitation.")}>Revoke invitation</button>}
                    </div>
                  ))}
                </div>
              )}
              {row.partners.length === 0 ? <p className="text-muted-foreground">No partner has joined yet.</p> : (
                <ul className="space-y-1" data-testid="shared-partner-list">
                  {row.partners.map((p) => (
                    <li key={p.access_id} className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate">{p.partner_name || p.partner_email}
                        <span className="text-muted-foreground"> · {p.document_ids?.length || 0} doc{(p.document_ids?.length || 0) === 1 ? "" : "s"}{p.show_updates ? " · updates" : ""}{p.access_expires_at ? ` · ${expiryLabel(p.access_expires_at)}` : ""}</span>
                      </span>
                      {!p.revoked && <button type="button" className="shrink-0 underline text-blue-700 disabled:opacity-50" disabled={!!busy} onClick={() => run(`toggle-${p.access_id}`, () => api.patch(`/bookings/${row.booking_id}/partner-access/${p.access_id}`, { active: p.active === false }), p.active === false ? "Partner access restored." : "Partner access deactivated.", "Could not update partner access.")} data-testid="shared-partner-toggle">{p.active === false ? "Restore" : "Deactivate"}</button>}
                      <button type="button" className="shrink-0 inline-flex items-center gap-1 underline text-muted-foreground hover:text-red-700 disabled:opacity-50"
                        disabled={!!busy} onClick={() => setConfirm({ kind: "partner", row, partner: p })} data-testid="shared-partner-revoke">
                        <UserMinus className="h-3.5 w-3.5" />Revoke
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="outline" className="border-zinc-300" disabled={!!busy} onClick={() => setManage(row)} data-testid="shared-manage">
                <Settings2 className="h-4 w-4 mr-1.5" />Manage
              </Button>
              {row.link && <Button type="button" size="sm" variant="outline" className="border-zinc-300" onClick={() => copyLink(row)} data-testid="shared-copy"><Copy className="h-4 w-4 mr-1.5" />Copy link</Button>}
              <Button type="button" size="sm" variant="outline" className="border-zinc-300" disabled={!!busy} onClick={() => regenerate(row)} data-testid="shared-regenerate">
                {busy === `regen-${row.booking_id}` ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : <RefreshCw className="h-4 w-4 mr-1.5" />}New link
              </Button>
              <Button type="button" size="sm" variant="outline" className="border-red-200 text-red-700 hover:bg-red-50" disabled={!!busy} onClick={() => setConfirm({ kind: "all", row })} data-testid="shared-revoke-all">
                <ShieldOff className="h-4 w-4 mr-1.5" />Stop sharing
              </Button>
            </div>
          </div>
        ))}
      </div>

      <AlertDialog open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)}>
        <AlertDialogContent className="bg-white">
          <AlertDialogHeader>
            <AlertDialogTitle>{confirm?.kind === "all" ? "Stop sharing this booking?" : "Remove this partner?"}</AlertDialogTitle>
            <AlertDialogDescription>
              {confirm?.kind === "all"
                ? "The invite link stops working and every partner immediately loses access to this booking and its shared documents."
                : `${confirm?.partner?.partner_name || confirm?.partner?.partner_email || "This partner"} immediately loses access. They cannot return with the old link.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={doConfirm} className="bg-red-600 hover:bg-red-700 text-white" data-testid="shared-confirm">{confirm?.kind === "all" ? "Stop sharing" : "Remove partner"}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={!!manage} onOpenChange={(o) => { if (!o) { setManage(null); onChanged(); } }}>
        <DialogContent className="bg-white max-w-lg max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Manage sharing</DialogTitle>
            <DialogDescription>{manage?.service} · {manage?.booking_no}</DialogDescription>
          </DialogHeader>
          {manage && <PartnerSharePanel bookingId={manage.booking_id} bookingNo={manage.booking_no} service={manage.service} />}
        </DialogContent>
      </Dialog>
    </>
  );
}

export default function SharedBookingsPanel({ limit = 0, defaultTab = "with-me", showViewAll = false, className = "" }) {
  const [tab, setTab] = useState(defaultTab);
  const [withMe, setWithMe] = useState(null);
  const [byMe, setByMe] = useState(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [a, b] = await Promise.allSettled([api.get("/partner/shared-bookings"), api.get("/partner/my-shares")]);
    setWithMe(a.status === "fulfilled" ? (a.value.data?.data || []) : []);
    setByMe(b.status === "fulfilled" ? (b.value.data?.data || []) : []);
    setError(a.status === "rejected" && b.status === "rejected" ? "Could not load shared bookings right now." : "");
  }, []);

  useEffect(() => { load(); }, [load]);

  const tabs = [
    { id: "with-me", label: "Shared with me", count: withMe?.length },
    { id: "by-me", label: "Shared by me", count: byMe?.length },
  ];

  return (
    <section className={`rounded-2xl border border-zinc-200 bg-white p-4 sm:p-5 ${className}`} data-testid="shared-bookings-section">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h2 className="font-heading text-lg font-semibold text-[#0A2540] flex items-center gap-2"><Users className="h-5 w-5 text-brand-hover" />Shared Bookings</h2>
        {showViewAll && <Link to="/customer/shared-bookings" className="text-sm text-brand-hover hover:underline" data-testid="shared-view-all">View all</Link>}
      </div>
      <div className="flex gap-1 rounded-lg bg-zinc-100 p-1 mb-4 w-full sm:w-fit" role="tablist">
        {tabs.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
            className={`flex-1 sm:flex-none px-3 py-1.5 rounded-md text-sm font-medium ${tab === t.id ? "bg-white text-zinc-900 shadow-sm" : "text-zinc-600 hover:text-zinc-900"}`}
            data-testid={`shared-tab-${t.id}`}>
            {t.label}{typeof t.count === "number" ? ` (${t.count})` : ""}
          </button>
        ))}
      </div>
      {error ? <p className="text-sm text-red-600">{error}</p> : (tab === "with-me" ? withMe : byMe) === null ? <Spinner /> : tab === "with-me"
        ? <SharedWithMe rows={withMe} limit={limit} />
        : <SharedByMe rows={byMe} limit={limit} onChanged={load} />}
    </section>
  );
}
