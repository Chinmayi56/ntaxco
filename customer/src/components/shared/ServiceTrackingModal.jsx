
import { useEffect, useState } from "react";
import {
  Check,
  Circle,
  CircleDot,
  Clock3,
  CreditCard,
  Download,
  FileCheck2,
  Loader2,
  RefreshCw,
  Sparkles,
  UserRound,
  BriefcaseBusiness,
} from "lucide-react";

import api from "@/lib/api";
import { toast } from "sonner";
import { StatusBadge, PriorityBadge } from "@/components/shared/StatusBadge";
import { Button } from "@/components/ui/button";
import { exportPDF } from "@/lib/exports";
import PartnerSharePanel from "@/components/shared/PartnerSharePanel";
import { inr } from "@/lib/utils";

function isPaid(booking) {
  return (
    String(booking?.payment_status || "").toLowerCase() === "paid"
  );
}

function isAssigned(booking) {
  return Boolean(
    booking?.assigned_agent || booking?.assigned_employee
  );
}

function isCompleted(booking) {
  return (
    String(booking?.status || "").toLowerCase() === "completed"
  );
}

function isCancelled(booking) {
  return (
    String(booking?.status || "").toLowerCase() === "cancelled"
  );
}

function isProcessing(booking) {
  return ["running", "processing", "confirmed"].includes(
    String(booking?.status || "").toLowerCase()
  );
}

function buildTimeline(booking) {
  const paid = isPaid(booking);
  const assigned = isAssigned(booking);
  const processing = isProcessing(booking);
  const completed = isCompleted(booking);

  return [
    {
      id: "booking",
      label: "Booking received",
      note: "Your service request has been recorded.",
      date: booking.created_at || booking.booking_date,
      done: true,
      icon: Check,
    },
    {
      id: "payment",
      label: "Payment confirmation",
      note: paid
        ? "Your payment has been received."
        : "Waiting for payment confirmation.",
      done: paid,
      icon: CreditCard,
    },
    {
      id: "consultant",
      label: "Consultant assigned",
      note: assigned
        ? `Assigned to ${
            booking.assigned_agent || booking.assigned_employee
          }.`
        : "Your consultant has not been assigned yet.",
      done: assigned,
      icon: UserRound,
    },
    {
      id: "processing",
      label: "Service processing",
      note: completed
        ? "Service processing is complete."
        : processing
          ? "Your service is being processed."
          : "Work has not started yet.",
      done: processing || completed,
      icon: BriefcaseBusiness,
    },
    {
      id: "completed",
      label: "Service completed",
      note: completed
        ? "Your service has been completed."
        : "Final completion confirmation will appear here.",
      done: completed,
      icon: FileCheck2,
    },
  ];
}

function formatDate(value) {
  if (!value) return "—";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  return date.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export default function ServiceTrackingModal({
  bookingId,
  open,
  onOpenChange,
  inline = false,
}) {
  const [booking, setBooking] = useState(null);
  const [invoice, setInvoice] = useState(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    if (!open || !bookingId) {
      setBooking(null);
      setInvoice(null);
      setLoading(false);
      return undefined;
    }

    let active = true;

    const loadBooking = async (silent = false) => {
      if (!silent) setLoading(true);

      try {
        const [{ data: bookingResponse }, { data: invoiceResponse }] =
          await Promise.all([
            api.get(`/bookings/${bookingId}`),
            api.get("/invoices"),
          ]);

        if (!active) return;

        const latestBooking = bookingResponse?.data;

        setBooking(latestBooking || null);

        const invoices = invoiceResponse?.data || [];

        const matchedInvoice = invoices.find(
          (item) =>
            String(item.booking_id) === String(bookingId)
        );

        setInvoice(matchedInvoice || null);
      } catch (error) {
        // The shared API layer handles standard API error reporting.
        if (!silent && active) {
          setBooking(null);
        }
      } finally {
        if (active && !silent) {
          setLoading(false);
        }
      }
    };

    loadBooking(false);

    // Preserve the existing automatic refresh behavior.
    const timer = window.setInterval(() => {
      if (!document.hidden) {
        loadBooking(true);
      }
    }, 10000);

    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [bookingId, open]);

  const handleRefresh = async () => {
    if (!bookingId || refreshing) return;

    setRefreshing(true);

    try {
      const [{ data: bookingResponse }, { data: invoiceResponse }] =
        await Promise.all([
          api.get(`/bookings/${bookingId}`),
          api.get("/invoices"),
        ]);

      const latestBooking = bookingResponse?.data;

      setBooking(latestBooking || null);

      const invoices = invoiceResponse?.data || [];

      setInvoice(
        invoices.find(
          (item) =>
            String(item.booking_id) === String(bookingId)
        ) || null
      );

      toast.success("Booking status refreshed.");
    } catch {
      toast.error("Could not refresh the booking status.");
    } finally {
      setRefreshing(false);
    }
  };

  const timeline = booking ? buildTimeline(booking) : [];
  const cancelled = booking && isCancelled(booking);

  const completedCount = timeline.filter(
    (step) => step.done
  ).length;

  const progress = timeline.length
    ? Math.round((completedCount / timeline.length) * 100)
    : 0;

  const currentIndex = timeline.findIndex(
    (step) => !step.done
  );

  const amount =
    booking?.price_snapshot ??
    booking?.confirmed_amount ??
    booking?.estimated_fee;

  const bookingReference =
    booking?.booking_no || booking?.id || bookingId;

  // This component intentionally renders as an ordinary page panel.
  // It does not use Dialog, a backdrop, or a fixed-position overlay.
  if (!inline) {
    return null;
  }

  return (
    <section
      className="ntaxco-inline-tracker overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
      aria-label="Track your service"
      data-testid="service-tracking-panel"
    >
      <header className="border-b border-slate-100 bg-white px-5 py-5">
        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-blue-600">
          NTAXCO · LIVE TRACKING
        </p>

        <div className="mt-1 flex items-start justify-between gap-2">
          <div>
            <h2 className="text-xl font-bold tracking-tight text-slate-900">
              Track your service
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Follow every stage of your booking.
            </p>
          </div>

          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-600 text-white shadow-lg shadow-blue-100">
            <Sparkles className="h-5 w-5" />
          </span>
        </div>
      </header>

      {!bookingId ? (
        <div className="flex min-h-[320px] flex-col items-center justify-center px-6 py-10 text-center">
          <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-50 text-blue-600">
            <Clock3 className="h-7 w-7" />
          </span>
          <h3 className="mt-4 font-semibold text-slate-900">
            Your tracking journey starts here
          </h3>
          <p className="mt-2 max-w-[260px] text-sm leading-6 text-slate-500">
            Book a service to see its progress, payment confirmation
            and consultant assignment here.
          </p>
        </div>
      ) : loading || !booking ? (
        <div className="flex min-h-[320px] flex-col items-center justify-center gap-3 px-6 text-center">
          <Loader2 className="h-7 w-7 animate-spin text-blue-600" />
          <p className="text-sm font-medium text-slate-700">
            Loading your booking…
          </p>
          <p className="text-xs text-slate-400">
            Connecting to your service record.
          </p>
        </div>
      ) : (
        <div className="space-y-4 p-4">
          {/* Booking summary */}
          <div className="rounded-2xl border border-blue-100 bg-gradient-to-br from-blue-50 via-white to-sky-50 p-4">
            <p className="text-xs font-medium text-slate-500">
              Booking reference
            </p>

            <h3 className="mt-1 break-words text-lg font-bold text-slate-900">
              {bookingReference}
            </h3>

            <p className="mt-2 text-sm font-semibold leading-5 text-slate-700">
              {booking.service || booking.service_name || "Booked service"}
            </p>

            <div className="mt-3 flex flex-wrap gap-2">
              <StatusBadge value={booking.status} />
              <StatusBadge value={booking.payment_status} />
            </div>

            <div className="mt-4">
              <div className="mb-2 flex items-center justify-between gap-2 text-xs">
                <span className="font-medium text-slate-600">
                  Workflow progress
                </span>
                <span className="font-bold text-blue-700">
                  {cancelled ? "Cancelled" : `${progress}%`}
                </span>
              </div>

              <div
                className="h-2 overflow-hidden rounded-full bg-blue-100"
                role="progressbar"
                aria-label="Workflow progress"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={cancelled ? 0 : progress}
              >
                <div
                  className="ntaxco-inline-progress h-full rounded-full bg-gradient-to-r from-blue-600 to-cyan-400"
                  style={{
                    width: `${cancelled ? 0 : progress}%`,
                  }}
                />
              </div>
            </div>
          </div>

          {/* Animated service timeline */}
          <div className="rounded-2xl border border-slate-100 bg-white p-4">
            <div className="mb-5 flex items-center justify-between gap-2">
              <div>
                <h3 className="font-bold text-slate-900">
                  Service journey
                </h3>
                <p className="mt-1 text-xs text-slate-500">
                  Your booking, step by step
                </p>
              </div>

              <span className="shrink-0 rounded-full bg-blue-50 px-2.5 py-1 text-xs font-bold text-blue-700">
                {completedCount}/{timeline.length} steps
              </span>
            </div>

            {cancelled ? (
              <div className="rounded-xl border border-red-200 bg-red-50 p-3">
                <p className="text-sm font-semibold text-red-800">
                  Booking cancelled
                </p>
                <p className="mt-1 text-xs leading-5 text-red-700">
                  Contact NTAXCO if you need assistance with this booking.
                </p>
              </div>
            ) : (
              <div>
                {timeline.map((step, index) => {
                  const active =
                    index === currentIndex && !step.done;

                  const Icon = step.icon;

                  return (
                    <div
                      key={step.id}
                      className="ntaxco-inline-step flex gap-3"
                      style={{
                        animationDelay: `${index * 90}ms`,
                      }}
                    >
                      <div className="flex w-8 shrink-0 flex-col items-center">
                        <div
                          className={[
                            "relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 transition-colors duration-500",
                            step.done
                              ? "border-emerald-500 bg-emerald-500 text-white"
                              : active
                                ? "ntaxco-inline-active border-blue-600 bg-white text-blue-600"
                                : "border-slate-200 bg-white text-slate-400",
                          ].join(" ")}
                        >
                          {step.done ? (
                            <Check
                              className="h-4 w-4"
                              strokeWidth={3}
                            />
                          ) : active ? (
                            <Icon className="h-4 w-4" />
                          ) : (
                            <CircleDot className="h-4 w-4" />
                          )}
                        </div>

                        {index < timeline.length - 1 && (
                          <div className="relative my-1 min-h-[44px] w-[2px] flex-1 overflow-hidden rounded-full bg-slate-100">
                            <div
                              className={`absolute inset-x-0 top-0 rounded-full transition-all duration-700 ${
                                step.done
                                  ? "h-full bg-emerald-500"
                                  : "h-0"
                              }`}
                            />

                            {active && (
                              <div className="ntaxco-inline-flow absolute inset-x-0 top-0 h-1/2 rounded-full bg-gradient-to-b from-blue-500 to-transparent" />
                            )}
                          </div>
                        )}
                      </div>

                      <div className="min-w-0 flex-1 pb-5">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <p
                            className={`text-sm font-semibold ${
                              step.done || active
                                ? "text-slate-900"
                                : "text-slate-400"
                            }`}
                          >
                            {step.label}
                          </p>

                          {step.done ? (
                            <span className="text-[9px] font-bold uppercase tracking-wider text-emerald-600">
                              Complete
                            </span>
                          ) : active ? (
                            <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-blue-50 px-2 py-1 text-[9px] font-bold uppercase tracking-wider text-blue-700">
                              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-blue-600" />
                              In progress
                            </span>
                          ) : null}
                        </div>

                        <p className="mt-1 text-xs leading-5 text-slate-500">
                          {step.note}
                        </p>

                        {step.id === "booking" && step.date && (
                          <p className="mt-1 text-[10px] text-slate-400">
                            {new Date(step.date).toLocaleString("en-IN")}
                          </p>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            <div className="rounded-xl bg-slate-50 p-3">
              <div className="flex items-start gap-2">
                {isCompleted(booking) ? (
                  <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                ) : (
                  <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-blue-600" />
                )}

                <p className="text-xs leading-5 text-slate-600">
                  {isCompleted(booking)
                    ? "Your service has been completed."
                    : cancelled
                      ? "This booking is cancelled."
                      : "Tracking automatically checks for status updates."}
                </p>
              </div>
            </div>
          </div>

          {/* Booking details */}
          <div className="rounded-2xl border border-slate-200 p-4">
            <h3 className="mb-3 text-sm font-bold text-slate-900">
              Booking details
            </h3>

            <div className="space-y-3 text-xs">
              <div className="flex items-start justify-between gap-3">
                <span className="text-slate-500">Booked on</span>
                <span className="text-right font-medium text-slate-800">
                  {formatDate(booking.booking_date || booking.created_at)}
                </span>
              </div>

              <div className="flex items-start justify-between gap-3">
                <span className="text-slate-500">Due date</span>
                <span className="text-right font-medium text-slate-800">
                  {formatDate(booking.due_date)}
                </span>
              </div>

              <div className="flex items-start justify-between gap-3">
                <span className="text-slate-500">Consultant</span>
                <span className="text-right font-medium text-slate-800">
                  {booking.assigned_agent ||
                    booking.assigned_employee ||
                    "Not assigned yet"}
                </span>
              </div>

              <div className="flex items-start justify-between gap-3">
                <span className="text-slate-500">Confirmed amount</span>
                <span className="text-right font-bold text-slate-900">
                  {amount != null ? inr(amount) : "—"}
                </span>
              </div>

              <div className="flex items-start justify-between gap-3">
                <span className="text-slate-500">Priority</span>
                <PriorityBadge value={booking.priority} />
              </div>
            </div>
          </div>

          {/* Existing invoice integration */}
          {invoice ? (
            <div className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 p-3">
              <div className="min-w-0">
                <p className="break-words text-sm font-semibold text-slate-900">
                  Invoice {invoice.invoice_no}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {inr(invoice.total)} · {invoice.payment_status}
                </p>
              </div>

              <Button
                variant="outline"
                size="sm"
                className="shrink-0 border-slate-300"
                onClick={() => {
                  exportPDF(
                    `Invoice ${invoice.invoice_no}`,
                    [
                      { key: "k", label: "Field" },
                      { key: "v", label: "Value" },
                    ],
                    [
                      { k: "Invoice No.", v: invoice.invoice_no },
                      { k: "Booking", v: bookingReference },
                      { k: "Amount", v: inr(invoice.amount) },
                      { k: "GST", v: inr(invoice.gst) },
                      { k: "Total", v: inr(invoice.total) },
                      { k: "Status", v: invoice.payment_status },
                      { k: "Issue Date", v: invoice.issue_date },
                    ]
                  );
                }}
                data-testid="tracking-download-invoice"
              >
                <Download className="mr-1.5 h-4 w-4" />
                Invoice
              </Button>
            </div>
          ) : (
            <p className="px-2 text-center text-xs leading-5 text-slate-500">
              No invoice is linked to this booking yet.
            </p>
          )}

          {String(booking.status || "").toLowerCase() !== "cancelled" && (
            <PartnerSharePanel
              bookingId={booking.id}
              bookingNo={bookingReference}
              service={booking.service}
            />
          )}

          <button
            type="button"
            onClick={handleRefresh}
            disabled={refreshing}
            className="flex w-full items-center justify-center gap-2 rounded-xl border border-slate-200 px-4 py-3 text-sm font-semibold text-slate-700 transition hover:border-blue-300 hover:bg-blue-50 disabled:opacity-60"
          >
            <RefreshCw
              className={`h-4 w-4 ${
                refreshing ? "animate-spin" : ""
              }`}
            />
            Refresh booking status
          </button>
        </div>
      )}

      <style>{`
        .ntaxco-inline-step {
          animation: ntaxco-inline-enter 380ms ease-out both;
        }

        .ntaxco-inline-active {
          animation: ntaxco-inline-pulse 1.8s ease-in-out infinite;
        }

        .ntaxco-inline-flow {
          animation: ntaxco-inline-flow 1.4s linear infinite;
        }

        .ntaxco-inline-progress {
          transition: width 650ms cubic-bezier(.2,.75,.25,1);
        }

        @keyframes ntaxco-inline-enter {
          from { opacity: 0; transform: translateY(7px); }
          to { opacity: 1; transform: translateY(0); }
        }

        @keyframes ntaxco-inline-pulse {
          0%, 100% {
            box-shadow: 0 0 0 0 rgba(37,99,235,.20);
          }
          50% {
            box-shadow: 0 0 0 6px rgba(37,99,235,.08);
          }
        }

        @keyframes ntaxco-inline-flow {
          from { transform: translateY(-100%); }
          to { transform: translateY(200%); }
        }

        @media (prefers-reduced-motion: reduce) {
          .ntaxco-inline-step,
          .ntaxco-inline-active,
          .ntaxco-inline-flow {
            animation: none !important;
          }

          .ntaxco-inline-progress {
            transition: none !important;
          }
        }
      `}</style>
    </section>
  );
}
