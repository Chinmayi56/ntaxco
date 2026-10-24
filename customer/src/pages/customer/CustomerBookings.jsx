
import { useEffect, useState } from "react";
import {
  CalendarCheck,
  CheckCircle2,
  Clock,
  Loader2,
  Plus,
  ListChecks,
} from "lucide-react";

import api, { describeApiError } from "@/lib/api";
import { toast } from "sonner";
import { useCrud } from "@/hooks/useCrud";
import PageHeader from "@/components/shared/PageHeader";
import DataTable from "@/components/shared/DataTable";
import { KpiCard } from "@/components/shared/KpiCard";
import {
  StatusBadge,
  PriorityBadge,
} from "@/components/shared/StatusBadge";
import { Button } from "@/components/ui/button";
import BookServiceModal from "@/components/shared/BookServiceModal";
import ServiceTrackingModal from "@/components/shared/ServiceTrackingModal";

export default function CustomerBookings() {
  const { rows, loading } = useCrud("bookings");
  const [open, setOpen] = useState(false);
  const [track, setTrack] = useState(null);
  const [primeBookings, setPrimeBookings] = useState([]);

  // Select the first booking by default so the tracking panel is visible
  // immediately, without requiring the customer to open a popup.
  useEffect(() => {
    if (!rows.length) {
      setTrack(null);
      return;
    }

    setTrack((current) => {
      const exists = rows.some(
        (booking) => String(booking.id) === String(current)
      );

      return exists ? current : rows[0].id;
    });
  }, [rows]);

  useEffect(() => {
    let active = true;

    api
      .get("/prime-service-bookings/my")
      .then((response) => {
        if (active) {
          setPrimeBookings(response.data || []);
        }
      })
      .catch((error) => {
        if (active) {
          toast.error(
            describeApiError(
              error,
              "Could not load Prime Service bookings."
            )
          );
        }
      });

    return () => {
      active = false;
    };
  }, []);

  const sorted = [...rows].sort((a, b) =>
    (b.created_at || "").localeCompare(a.created_at || "")
  );

  const columns = [
    { key: "booking_no", label: "Booking" },
    {
      key: "service",
      label: "Service",
      render: (row) => (
        <div>
          <div>{row.service || row.service_name || "Service"}</div>
          {(row.booking_type || row.service_tier) && (
            <span
              className={`text-xs font-semibold ${
                String(row.booking_type || row.service_tier)
                  .toLowerCase()
                  .includes("prime")
                  ? "text-amber-700"
                  : "text-zinc-500"
              }`}
            >
              {row.booking_type || row.service_tier}
            </span>
          )}
        </div>
      ),
    },
    {
      key: "assigned_agent",
      label: "Consultant",
      render: (row) =>
        row.assigned_agent || row.assigned_employee || "—",
    },
    { key: "booking_date", label: "Booked" },
    {
      key: "due_date",
      label: "Due",
      render: (row) => row.due_date || "—",
    },
    {
      key: "priority",
      label: "Priority",
      render: (row) => <PriorityBadge value={row.priority} />,
    },
    {
      key: "status",
      label: "Status",
      render: (row) => <StatusBadge value={row.status} />,
    },
    {
      key: "payment_status",
      label: "Payment",
      render: (row) => <StatusBadge value={row.payment_status} />,
    },
    {
      key: "confirmed_amount",
      label: "Confirmed amount",
      render: (row) => {
        const amount =
          row.price_snapshot ??
          row.confirmed_amount ??
          row.estimated_fee;

        return amount != null
          ? `₹${Number(amount).toLocaleString("en-IN")}`
          : "—";
      },
    },
    {
      key: "processing_snapshot",
      label: "Estimated processing",
      render: (row) =>
        row.processing_time_snapshot || row.processing_time || "—",
    },
    {
      key: "actions",
      label: "Actions",
      render: (row) => (
        <Button
          size="sm"
          variant="outline"
          className={`h-8 ${
            String(track) === String(row.id)
              ? "border-blue-600 bg-blue-50 text-blue-700"
              : "border-zinc-300"
          }`}
          onClick={() => setTrack(row.id)}
          data-testid={`track-${row.id}`}
        >
          <ListChecks className="mr-1 h-3.5 w-3.5" />
          {String(track) === String(row.id) ? "Tracking" : "Track"}
        </Button>
      ),
    },
  ];

  return (
    <div className="mx-auto max-w-[1800px] px-4 py-6 sm:px-6">
      <PageHeader
        title="My Services"
        breadcrumb={["Customer", "My Services"]}
        subtitle="View your bookings and track the progress of every service."
        actions={
          <Button
            className="bg-royal font-semibold text-white hover:bg-royal-hover"
            onClick={() => setOpen(true)}
            data-testid="new-booking-btn"
          >
            <Plus className="mr-1.5 h-4 w-4" />
            Book a Service
          </Button>
        }
      />

      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-[minmax(0,1fr)_400px]">
        {/* LEFT: Existing booking workspace */}
        <div className="min-w-0 space-y-6">
          <div className="grid grid-cols-2 gap-3 2xl:grid-cols-4">
            <KpiCard
              title="Total Services"
              value={rows.length}
              icon={CalendarCheck}
              loading={loading}
              testId="bk-total"
            />
            <KpiCard
              title="Completed"
              value={
                rows.filter(
                  (row) =>
                    String(row.status || "").toLowerCase() ===
                    "completed"
                ).length
              }
              icon={CheckCircle2}
              loading={loading}
              testId="bk-done"
            />
            <KpiCard
              title="In Progress"
              value={
                rows.filter((row) =>
                  ["running", "confirmed", "processing"].includes(
                    String(row.status || "").toLowerCase()
                  )
                ).length
              }
              icon={Loader2}
              loading={loading}
              testId="bk-running"
            />
            <KpiCard
              title="Pending"
              value={
                rows.filter(
                  (row) =>
                    String(row.status || "").toLowerCase() ===
                    "pending"
                ).length
              }
              icon={Clock}
              loading={loading}
              testId="bk-pending"
            />
          </div>

          <div className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white">
            <DataTable
              title="My Services"
              columns={columns}
              rows={sorted}
              loading={loading}
              pageSize={8}
              testId="customer-bookings-table"
            />
          </div>

          {/* Existing Prime Service Requests section */}
          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <div className="border-b p-5">
              <h2 className="text-lg font-bold">
                Prime Service Requests
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                Status updates for your GST, TDS, ROC, Income Tax
                and Payroll requests.
              </p>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-left">
                  <tr>
                    {[
                      "Reference",
                      "Service",
                      "Booked on",
                      "Status",
                      "Pricing",
                    ].map((heading) => (
                      <th key={heading} className="p-3 font-semibold">
                        {heading}
                      </th>
                    ))}
                  </tr>
                </thead>

                <tbody>
                  {primeBookings.map((booking) => (
                    <tr key={booking.id} className="border-t">
                      <td className="p-3 font-semibold">
                        {booking.booking_reference}
                      </td>
                      <td className="p-3">
                        {booking.service_type}
                      </td>
                      <td className="p-3">
                        {booking.created_at
                          ? new Date(
                              booking.created_at
                            ).toLocaleDateString("en-IN")
                          : "—"}
                      </td>
                      <td className="p-3">
                        <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-800">
                          {booking.status}
                        </span>
                      </td>
                      <td className="p-3">
                        {booking.quoted_price != null
                          ? `₹${Number(
                              booking.quoted_price
                            ).toLocaleString("en-IN")}`
                          : booking.pricing_label ||
                            "Quotation required"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {!primeBookings.length && (
                <p className="p-6 text-sm text-slate-500">
                  No Prime Service requests yet.
                </p>
              )}
            </div>
          </section>
        </div>

        {/* RIGHT: Always-visible tracking panel, not a modal */}
        <aside className="min-w-0 xl:sticky xl:top-4">
          <ServiceTrackingModal
            bookingId={track}
            open={Boolean(track)}
            onOpenChange={(isOpen) => {
              if (!isOpen) setTrack(null);
            }}
            inline
          />
        </aside>
      </div>

      <BookServiceModal open={open} onOpenChange={setOpen} />
    </div>
  );
}
