import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Check, ChevronDown, Copy, Link2, Loader2, Mail, MessageCircle, Share2, Users } from "lucide-react";
import api, { describeApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ACCESS_PERIOD_OPTIONS, LINK_EXPIRY_OPTIONS, expiryLabel, formatShareDate } from "@/lib/partnerShare";

// "Share with Partner": the link and its booking relationship live in MongoDB
// (POST/GET/DELETE /api/bookings/{id}/partner-share). Nothing is kept in
// localStorage - on reload the panel asks the backend for the booking's link.
// The partner opens it at /partner/booking/<token>, must sign in / register with their OWN account, and the backend then
// grants that account (only) read access. A link serves one partner; generate another link for the next partner.
// GET/DELETE /api/bookings/{id}/partner-access lists / removes the partners who currently have access;
// PATCH .../partner-access/{access_id} changes what one partner may see (status updates, individual documents).
// Documents are never shared by default - the owner ticks each one. Invoices and payment history are never shareable.

function absoluteUrl(share) {
  if (!share) return "";
  if (share.share_url) return share.share_url;
  return `${window.location.origin}${share.link_path}`;
}

async function copyText(text) {
  if (navigator.clipboard?.writeText && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  // Fallback for non-secure origins (e.g. LAN development over http).
  const el = document.createElement("textarea");
  el.value = text;
  el.setAttribute("readonly", "");
  el.style.position = "fixed";
  el.style.opacity = "0";
  document.body.appendChild(el);
  el.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(el);
  if (!ok) throw new Error("Copy is not supported in this browser.");
}

function DocPicker({ docs, selected, onToggle, disabled }) {
  if (!docs.length) return <p className="text-xs text-muted-foreground">You have no documents for this booking to share.</p>;
  return (
    <ul className="space-y-1.5 max-h-40 overflow-y-auto pr-1" data-testid="partner-doc-picker">
      {docs.map((d) => (
        <li key={d.doc_id}>
          <label className="flex items-start gap-2 text-xs text-zinc-700 cursor-pointer">
            <input type="checkbox" className="mt-0.5" checked={selected.includes(d.doc_id)} disabled={disabled} onChange={() => onToggle(d.doc_id)} />
            <span className="min-w-0 truncate">{d.name}{d.uploaded_date ? <span className="text-muted-foreground"> · {formatShareDate(d.uploaded_date)}</span> : null}</span>
          </label>
        </li>
      ))}
    </ul>
  );
}

function PartnerRow({ partner, docs, busy, onRemove, onSave }) {
  const [open, setOpen] = useState(false);
  const [showUpdates, setShowUpdates] = useState(partner.show_updates !== false);
  const [selected, setSelected] = useState(partner.document_ids || []);
  const toggle = (id) => setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  return (
    <li className="text-xs">
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0 truncate text-zinc-700">
          {partner.partner_name || partner.partner_email}{partner.partner_name && partner.partner_email ? ` (${partner.partner_email})` : ""}
          {partner.access_expires_at ? <span className="text-muted-foreground"> · access {expiryLabel(partner.access_expires_at)}</span> : null}
        </span>
        <span className="shrink-0 flex items-center gap-3">
          <button type="button" className="inline-flex items-center gap-0.5 underline text-muted-foreground hover:text-zinc-900" onClick={() => setOpen((o) => !o)} aria-expanded={open} data-testid="partner-permissions-toggle">
            Permissions<ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} />
          </button>
          <button type="button" className="underline text-muted-foreground hover:text-red-700 disabled:opacity-50" onClick={() => onRemove(partner.access_id)} disabled={busy} data-testid="partner-remove">Remove</button>
        </span>
      </div>
      {open && (
        <div className="mt-2 rounded-lg bg-zinc-50 border border-zinc-100 p-3 space-y-2" data-testid="partner-permissions">
          <label className="flex items-center gap-2 text-xs text-zinc-700"><input type="checkbox" checked={showUpdates} onChange={(e) => setShowUpdates(e.target.checked)} />Show status updates timeline</label>
          <p className="text-xs font-semibold text-zinc-700">Documents this partner can download</p>
          <DocPicker docs={docs} selected={selected} onToggle={toggle} disabled={busy} />
          <Button type="button" size="sm" className="bg-royal text-white hover:bg-royal-hover font-semibold" disabled={busy} onClick={() => onSave(partner.access_id, { show_updates: showUpdates, document_ids: selected })} data-testid="partner-permissions-save">Save permissions</Button>
        </div>
      )}
    </li>
  );
}

export default function PartnerSharePanel({ bookingId, bookingNo, service, className = "" }) {
  const [share, setShare] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [partnerEmail, setPartnerEmail] = useState("");
  const [partners, setPartners] = useState([]);
  const [docs, setDocs] = useState([]);
  const [linkDays, setLinkDays] = useState(30);
  const [accessDays, setAccessDays] = useState("");
  const [showUpdates, setShowUpdates] = useState(true);
  const [selectedDocs, setSelectedDocs] = useState([]);

  const loadDocs = useCallback(async () => {
    if (!bookingId) return;
    try {
      const { data } = await api.get(`/bookings/${bookingId}/partner-share/documents`);
      setDocs(data?.data || []);
    } catch (e) { setDocs([]); }
  }, [bookingId]);

  const loadPartners = useCallback(async () => {
    if (!bookingId) return;
    try {
      const { data } = await api.get(`/bookings/${bookingId}/partner-access`);
      setPartners(data?.data || []);
    } catch (e) { setPartners([]); }
  }, [bookingId]);

  const load = useCallback(async () => {
    if (!bookingId) { setLoading(false); return; }
    loadPartners();
    loadDocs();
    try {
      const { data } = await api.get(`/bookings/${bookingId}/partner-share`);
      setShare(data?.data || null);
    } catch (e) {
      // A missing/failed lookup just means "no link yet"; generating will surface real errors.
      setShare(null);
    } finally {
      setLoading(false);
    }
  }, [bookingId, loadPartners, loadDocs]);

  useEffect(() => { setLoading(true); setShare(null); setPartners([]); setCopied(false); setSelectedDocs([]); load(); }, [load]);

  const generate = async (regenerate = false) => {
    setBusy(true);
    try {
      // Regenerating rotates the current link, so it keeps that link's email restriction.
      const email = regenerate ? (share?.partner_email || "") : partnerEmail.trim().toLowerCase();
      if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        toast.error("Enter a valid partner email before generating an invitation.");
        return;
      }
      // Regenerating keeps the choices already stored on the link; a first link carries the owner's choices from this form.
      const choices = regenerate ? {} : {
        expires_in_days: Number(linkDays), show_updates: showUpdates, document_ids: selectedDocs,
        ...(accessDays ? { access_days: Number(accessDays) } : {}),
      };
      const { data } = await api.post(`/bookings/${bookingId}/partner-share`, { regenerate, ...choices, ...(email ? { partner_email: email } : {}) });
      setShare(data?.data || null);
      setPartnerEmail("");
      setCopied(false);
      toast.success(regenerate ? "New partner link generated. The old link no longer works." : "Partner link generated.");
    } catch (e) {
      toast.error(describeApiError(e, "Could not generate the partner link."));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async () => {
    setBusy(true);
    try {
      await api.delete(`/bookings/${bookingId}/partner-share`);
      setShare(null);
      setPartners([]);
      setCopied(false);
      toast.success("Sharing stopped. The link no longer works and all partners lost access.");
    } catch (e) {
      toast.error(describeApiError(e, "Could not stop sharing."));
    } finally {
      setBusy(false);
    }
  };

  const removePartner = async (accessId) => {
    setBusy(true);
    try {
      await api.delete(`/bookings/${bookingId}/partner-access/${accessId}`);
      setPartners((rows) => rows.filter((p) => p.access_id !== accessId));
      toast.success("Partner access removed.");
    } catch (e) {
      toast.error(describeApiError(e, "Could not remove this partner."));
    } finally {
      setBusy(false);
    }
  };

  const savePermissions = async (accessId, body) => {
    setBusy(true);
    try {
      const { data } = await api.patch(`/bookings/${bookingId}/partner-access/${accessId}`, body);
      setPartners((rows) => rows.map((p) => (p.access_id === accessId ? { ...p, ...(data?.data || {}) } : p)));
      toast.success("Permissions updated. The partner sees the change immediately.");
    } catch (e) {
      toast.error(describeApiError(e, "Could not update permissions."));
    } finally {
      setBusy(false);
    }
  };

  const url = absoluteUrl(share);
  const message = `I've booked "${service || "a service"}" with NTAXCO${bookingNo ? ` (Booking ${bookingNo})` : ""}. You can follow its progress here: ${url}`;

  const copy = async () => {
    if (!url) return;
    try {
      await copyText(url);
      setCopied(true);
      toast.success("Link copied.");
      window.setTimeout(() => setCopied(false), 2500);
    } catch (e) {
      toast.error("Could not copy automatically. Please select the link and copy it.");
    }
  };

  const canNativeShare = typeof navigator !== "undefined" && typeof navigator.share === "function";
  const nativeShare = async () => {
    try {
      await navigator.share({ title: "NTAXCO booking", text: message, url });
    } catch (e) {
      if (e?.name !== "AbortError") toast.error("Sharing was not completed.");
    }
  };

  return (
    <div className={`rounded-xl border border-zinc-200 p-4 text-left ${className}`} data-testid="partner-share">
      <div className="flex items-start gap-3">
        <div className="h-9 w-9 shrink-0 rounded-lg bg-brand-faint flex items-center justify-center">
          <Users className="h-5 w-5 text-brand-hover" />
        </div>
        <div className="min-w-0 flex-1">
          <h4 className="text-sm font-semibold text-zinc-900">Share with Partner</h4>
          <p className="text-xs text-muted-foreground mt-0.5">
            Give your partner a private link to follow this booking. They sign in or register with their own account, and only that account can then see it (read-only). Invoices and payment details are never shown; documents are shared only if you tick them.
          </p>
        </div>
      </div>

      {!share && (
        <div className="mt-3 space-y-3" data-testid="partner-options">
          <input
            type="email"
            required
            value={partnerEmail}
            onChange={(e) => setPartnerEmail(e.target.value)}
            placeholder="Partner email (required; invitation is bound to this account)"
            aria-label="Partner email"
            className="w-full rounded-md border border-zinc-200 px-3 py-2 text-xs text-zinc-700"
            data-testid="partner-email-input"
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="text-xs text-zinc-700">Link expires after
              <select value={linkDays} onChange={(e) => setLinkDays(e.target.value)} className="mt-1 w-full rounded-md border border-zinc-200 bg-white px-2 py-2 text-xs" data-testid="partner-link-expiry">
                {LINK_EXPIRY_OPTIONS.map((o) => <option key={o.days} value={o.days}>{o.label}</option>)}
              </select>
            </label>
            <label className="text-xs text-zinc-700">Partner access lasts
              <select value={accessDays} onChange={(e) => setAccessDays(e.target.value)} className="mt-1 w-full rounded-md border border-zinc-200 bg-white px-2 py-2 text-xs" data-testid="partner-access-period">
                {ACCESS_PERIOD_OPTIONS.map((o) => <option key={String(o.days)} value={o.days}>{o.label}</option>)}
              </select>
            </label>
          </div>
          <label className="flex items-center gap-2 text-xs text-zinc-700"><input type="checkbox" checked={showUpdates} onChange={(e) => setShowUpdates(e.target.checked)} data-testid="partner-show-updates" />Show status updates timeline</label>
          <div>
            <p className="text-xs font-semibold text-zinc-700 mb-1">Documents the partner can download (optional)</p>
            <DocPicker docs={docs} selected={selectedDocs} onToggle={(id) => setSelectedDocs((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))} disabled={busy} />
          </div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          className="bg-royal text-white hover:bg-royal-hover font-semibold"
          onClick={() => generate(false)}
          disabled={busy || loading || !!share || !bookingId || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(partnerEmail.trim())}
          data-testid="partner-generate"
        >
          {busy && !share ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : <Link2 className="h-4 w-4 mr-1.5" />}
          Generate Partner Link
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="border-zinc-300"
          onClick={copy}
          disabled={!share}
          data-testid="partner-copy"
        >
          {copied ? <Check className="h-4 w-4 mr-1.5 text-emerald-600" /> : <Copy className="h-4 w-4 mr-1.5" />}
          {copied ? "Copied" : "Copy Link"}
        </Button>
      </div>

      {share && (
        <div className="mt-3 space-y-3">
          <input
            readOnly
            value={url}
            onFocus={(e) => e.target.select()}
            aria-label="Partner link"
            className="w-full rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-700"
            data-testid="partner-link-input"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button asChild size="sm" variant="outline" className="border-zinc-300">
              <a href={`https://wa.me/?text=${encodeURIComponent(message)}`} target="_blank" rel="noopener noreferrer" data-testid="partner-whatsapp">
                <MessageCircle className="h-4 w-4 mr-1.5 text-emerald-600" />WhatsApp
              </a>
            </Button>
            <Button asChild size="sm" variant="outline" className="border-zinc-300">
              <a
                href={`mailto:?subject=${encodeURIComponent(`NTAXCO booking${bookingNo ? ` ${bookingNo}` : ""}`)}&body=${encodeURIComponent(message)}`}
                data-testid="partner-email"
              >
                <Mail className="h-4 w-4 mr-1.5" />Email
              </a>
            </Button>
            {canNativeShare && (
              <Button type="button" size="sm" variant="outline" className="border-zinc-300" onClick={nativeShare} data-testid="partner-native-share">
                <Share2 className="h-4 w-4 mr-1.5" />More
              </Button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {share.expires_at && <span data-testid="partner-link-expiry-text">Link {expiryLabel(share.expires_at)} ({formatShareDate(share.expires_at)})</span>}
            {share.document_ids?.length > 0 && <span>{share.document_ids.length} document{share.document_ids.length === 1 ? "" : "s"} included</span>}
            {share.partner_email && <span>For {share.partner_email}</span>}
            <button type="button" className="underline hover:text-zinc-900 disabled:opacity-50" onClick={() => generate(true)} disabled={busy} data-testid="partner-regenerate">
              Generate a new link
            </button>
            <button type="button" className="underline hover:text-red-700 disabled:opacity-50" onClick={revoke} disabled={busy} data-testid="partner-revoke">
              Stop sharing with everyone
            </button>
          </div>
        </div>
      )}

      {partners.length > 0 && (
        <div className="mt-4 border-t border-zinc-100 pt-3" data-testid="partner-access-list">
          <p className="text-xs font-semibold text-zinc-700 mb-2">Partners with access</p>
          <ul className="space-y-2.5">
            {partners.map((p) => <PartnerRow key={p.access_id} partner={p} docs={docs} busy={busy} onRemove={removePartner} onSave={savePermissions} />)}
          </ul>
        </div>
      )}
    </div>
  );
}
