import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ArrowLeft, CalendarCheck, Clock, Download, FileText, Loader2, ShieldCheck, UserCircle2 } from "lucide-react";
import api, { describeApiError } from "@/lib/api";
import PageHeader from "@/components/shared/PageHeader";
import SharedBookingsPanel from "@/components/shared/SharedBookingsPanel";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { Button } from "@/components/ui/button";
import { downloadDataUri, expiryLabel, formatShareDate, formatShareDateTime } from "@/lib/partnerShare";

// Shared Bookings: bookings other customers shared with THIS account (read-only) and the ones I shared with partners.
// The backend returns only what the signed-in account is authorized for; nothing here is a security boundary.

function Spinner() {
  return <div className="py-16 flex justify-center text-zinc-400"><Loader2 className="h-6 w-6 animate-spin" /></div>;
}

export default function CustomerSharedBookings() {
  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 py-8" data-testid="shared-bookings">
      <PageHeader title="Shared Bookings" subtitle="Bookings shared with you (read-only) and the bookings you shared with partners." />
      <SharedBookingsPanel />
    </div>
  );
}

export function SharedBookingDetail() {
  const { accessId } = useParams();
  const [state, setState] = useState({ loading: true, data: null, error: "" });
  const [downloading, setDownloading] = useState("");

  useEffect(() => {
    let active = true;
    setState({ loading: true, data: null, error: "" });
    api.get(`/partner/shared-bookings/${encodeURIComponent(accessId)}`)
      .then((r) => active && setState({ loading: false, data: r.data?.data || null, error: "" }))
      .catch((e) => active && setState({
        loading: false, data: null,
        error: e?.response?.status === 404 ? "This booking is not shared with your account, or access was withdrawn." : "Could not load this booking right now.",
      }));
    return () => { active = false; };
  }, [accessId]);

  const download = async (doc) => {
    setDownloading(doc.doc_id);
    try {
      const { data } = await api.get(`/partner/shared-bookings/${encodeURIComponent(accessId)}/documents/${encodeURIComponent(doc.doc_id)}`);
      const file = data?.data;
      downloadDataUri(file?.file_name || doc.file_name || doc.name, file?.file_data);
    } catch (e) {
      toast.error(e?.response?.status === 404 ? "This document is no longer shared with you." : describeApiError(e, "Could not download this document."));
    } finally {
      setDownloading("");
    }
  };

  const { loading, data, error } = state;
  const rows = data ? [
    ["Booking", data.booking_no],
    ["Service", data.service],
    ["Booked on", formatShareDate(data.booking_date) || data.booking_date],
    ["Due date", formatShareDate(data.due_date) || data.due_date],
    ["Estimated processing", data.estimated_processing],
    ["Consultant", data.consultant],
  ].filter((r) => r[1]) : [];

  return (
    <div className="max-w-2xl mx-auto px-4 py-8 sm:py-10" data-testid="shared-booking-detail">
      <Button asChild variant="ghost" size="sm" className="mb-3 -ml-2"><Link to="/customer/shared-bookings"><ArrowLeft className="h-4 w-4 mr-1" />Shared Bookings</Link></Button>
      <div className="rounded-2xl border border-zinc-200 bg-white p-4 sm:p-6 shadow-sm">
        {loading ? <Spinner /> : error ? (
          <p className="text-sm text-zinc-700 text-center py-6" data-testid="shared-booking-error">{error}</p>
        ) : (
          <>
            <div className="flex items-center gap-2 text-brand-hover mb-1"><CalendarCheck className="h-5 w-5" /><h1 className="font-heading text-lg font-bold text-[#0A2540]">Shared Booking</h1></div>
            {data.shared_by && <p className="text-xs text-muted-foreground flex items-center gap-1.5 mb-1"><UserCircle2 className="h-4 w-4" />Shared with you by {data.shared_by}</p>}
            {data.access_expires_at && <p className="text-xs text-amber-700 flex items-center gap-1.5 mb-3" data-testid="shared-access-expiry"><Clock className="h-4 w-4" />Your access {expiryLabel(data.access_expires_at)}</p>}
            <div className="rounded-xl border border-zinc-200 p-4 text-sm space-y-2 mt-3">
              {rows.map(([k, v]) => (
                <div key={k} className="flex justify-between border-b border-zinc-100 last:border-0 pb-1.5">
                  <span className="text-muted-foreground">{k}</span>
                  <span className="font-medium text-zinc-900 text-right ml-4 break-words">{v}</span>
                </div>
              ))}
              {data.status && <div className="flex justify-between items-center pt-0.5"><span className="text-muted-foreground">Status</span><StatusBadge value={data.status} /></div>}
            </div>

            <h2 className="mt-6 mb-2 text-sm font-semibold text-zinc-900 flex items-center gap-2"><Clock className="h-4 w-4 text-brand-hover" />Updates</h2>
            {!data.can_view_updates ? (
              <p className="text-xs text-muted-foreground" data-testid="shared-updates-off">The customer has not shared the updates timeline.</p>
            ) : data.updates?.length ? (
              <ol className="space-y-2 border-l border-zinc-200 pl-4" data-testid="shared-updates">
                {data.updates.map((u, i) => (
                  <li key={`${u.at}-${i}`} className="relative text-sm">
                    <span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-brand-hover" />
                    <p className="font-medium text-zinc-900">{u.title}</p>
                    <p className="text-xs text-muted-foreground">{formatShareDateTime(u.at)}</p>
                  </li>
                ))}
              </ol>
            ) : <p className="text-xs text-muted-foreground">No updates yet.</p>}

            <h2 className="mt-6 mb-2 text-sm font-semibold text-zinc-900 flex items-center gap-2"><FileText className="h-4 w-4 text-brand-hover" />Documents</h2>
            {data.documents?.length ? (
              <ul className="space-y-2" data-testid="shared-documents">
                {data.documents.map((d) => (
                  <li key={d.doc_id} className="flex items-center justify-between gap-3 rounded-lg border border-zinc-200 px-3 py-2">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-zinc-900 truncate">{d.name}</p>
                      <p className="text-xs text-muted-foreground truncate">{[d.file_name, formatShareDate(d.uploaded_date)].filter(Boolean).join(" · ")}</p>
                    </div>
                    <Button type="button" size="sm" variant="outline" className="shrink-0 border-zinc-300" disabled={downloading === d.doc_id} onClick={() => download(d)} data-testid="shared-document-download">
                      {downloading === d.doc_id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4 sm:mr-1.5" />}<span className="hidden sm:inline">Download</span>
                    </Button>
                  </li>
                ))}
              </ul>
            ) : <p className="text-xs text-muted-foreground" data-testid="shared-documents-empty">The customer has not shared any documents for this booking.</p>}

            <p className="text-xs text-muted-foreground mt-6 flex items-center justify-center gap-1.5 text-center"><ShieldCheck className="h-3.5 w-3.5 shrink-0" />Read-only view. Invoices, payments and other documents are never shown.</p>
          </>
        )}
      </div>
    </div>
  );
}
