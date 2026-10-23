import CrudModule from "@/components/shared/CrudModule";
import { StatusBadge, PriorityBadge } from "@/components/shared/StatusBadge";
import ServiceBreakdown from "@/components/shared/ServiceBreakdown";
import { inr } from "@/lib/utils";
import { useState } from "react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CalendarCheck, CalendarClock, Clock, CheckCircle2, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import BookingChat from "@/components/shared/BookingChat";
import DragDropWorkflow from "@/components/shared/DragDropWorkflow";
import { TaskCreateDialog } from "@/pages/admin/TaskBoard";

const STATUS = ["Pending", "Running", "Confirmed", "Completed", "Cancelled"];
const PRIORITY = ["Low", "Medium", "High"];
const PAY = ["Paid", "Pending", "Partial"];

const todayStr = () => new Date().toISOString().slice(0, 10);

export default function Bookings() {
  const [chat, setChat] = useState(null);
  const [taskBooking, setTaskBooking] = useState(null);
  const [bookingTypeFilter, setBookingTypeFilter] = useState("all");
  return (
    <>
    <CrudModule
      rowActions={(r) => (
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="icon" className="h-8 w-8" title="Create Task" onClick={() => setTaskBooking(r)} data-testid={`create-task-${r.id}`}><CalendarClock className="h-4 w-4 text-royal" /></Button>
          <Button variant="ghost" size="icon" className="h-8 w-8" title="Chat" onClick={() => setChat(r)} data-testid={`chat-${r.id}`}><MessageSquare className="h-4 w-4 text-royal" /></Button>
        </div>
      )}
      rowFilter={(items) => [...items]
        .filter((row) => bookingTypeFilter === "all" || String(row.booking_type || "").toLowerCase() === bookingTypeFilter)
        .sort((a, b) => { const at = Date.parse(a.created_at || a.createdAt || a.booking_date || "") || 0; const bt = Date.parse(b.created_at || b.createdAt || b.booking_date || "") || 0; return bt - at || String(b.id || b.booking_no || "").localeCompare(String(a.id || a.booking_no || "")); })}
      title="Bookings"
      singular="Booking"
      name="bookings"
      fetchAll={true}
      breadcrumb={["Super Admin", "Bookings"]}
      subtitle="All service bookings — status, priority and payment, live from the NTAXCO backend."
      kpiFn={(rows) => {
        const today = todayStr();
        return [
          { title: "Total Bookings", value: rows.length, icon: CalendarCheck, tone: "royal", filterFn: () => true },
          { title: "Today's Bookings", value: rows.filter((r) => (r.booking_date || r.created_at || "").slice(0, 10) === today).length, icon: CalendarClock, tone: "royal", filterFn: (r) => (r.booking_date || r.created_at || "").slice(0, 10) === today },
          { title: "New Bookings", value: rows.filter((r) => String(r.status || "").toLowerCase() === "pending").length, icon: Clock, tone: "red", filterFn: (r) => String(r.status || "").toLowerCase() === "pending" },
          { title: "Confirmed", value: rows.filter((r) => String(r.status || "").toLowerCase() === "confirmed").length, icon: CalendarCheck, tone: "royal", filterFn: (r) => String(r.status || "").toLowerCase() === "confirmed" },
          { title: "Running", value: rows.filter((r) => ["running", "in progress", "in_progress"].includes(String(r.status || "").toLowerCase())).length, icon: Clock, tone: "amber", filterFn: (r) => ["running", "in progress", "in_progress"].includes(String(r.status || "").toLowerCase()) },
          { title: "Completed", value: rows.filter((r) => String(r.status || "").toLowerCase() === "completed").length, icon: CheckCircle2, tone: "green", filterFn: (r) => String(r.status || "").toLowerCase() === "completed" },
          { title: "Cancelled", value: rows.filter((r) => String(r.status || "").toLowerCase() === "cancelled").length, icon: CheckCircle2, tone: "grey", filterFn: (r) => String(r.status || "").toLowerCase() === "cancelled" },
          { title: "Payment Pending", value: rows.filter((r) => ["pending", "unpaid", "partial"].includes(String(r.payment_status || "").toLowerCase())).length, icon: Clock, tone: "amber", filterFn: (r) => ["pending", "unpaid", "partial"].includes(String(r.payment_status || "").toLowerCase()) },
          { title: "Paid", value: rows.filter((r) => String(r.payment_status || "").toLowerCase() === "paid").length, icon: CheckCircle2, tone: "green", filterFn: (r) => String(r.payment_status || "").toLowerCase() === "paid" },
        ];
      }}
      extra={(rows, loading, load) => (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3 rounded-xl border border-zinc-200 bg-white p-3">
            <label htmlFor="booking-type-filter" className="text-sm font-medium text-zinc-700">Booking type</label>
            <Select value={bookingTypeFilter} onValueChange={setBookingTypeFilter}>
              <SelectTrigger id="booking-type-filter" className="w-full sm:w-56" data-testid="booking-type-filter"><SelectValue placeholder="All booking types" /></SelectTrigger>
              <SelectContent className="bg-white">
                <SelectItem value="all">All</SelectItem>
                <SelectItem value="individual">Individual</SelectItem>
                <SelectItem value="business">NTAXCO for Business</SelectItem>
              </SelectContent>
            </Select>
            {bookingTypeFilter !== "all" && <Button variant="ghost" size="sm" onClick={() => setBookingTypeFilter("all")}>Clear filter</Button>}
          </div>
          <ServiceBreakdown rows={rows} field="service" title="Bookings by Service" testId="bookings-service-breakdown" />
          <DragDropWorkflow
            title="Booking → Service"
            description="Optional service reassignment for bookings. Only canonical backend services are accepted."
            sourceRows={rows}
            sourceType="booking"
            targetType="service"
            sourceLabel="booking"
            getSourceLabel={(r) => `${r.booking_no || r.id} · ${r.customer || "Customer"}`}
            getTargetLabel={(r) => r.name || r.title || r.category || r.id}
            getTargetValue={(r) => r.id}
            onRefresh={load}
          />
        </div>
      )}

      columns={[
        { key: "booking_no", label: "Booking No" },
        { key: "customer", label: "Customer" },
        { key: "service", label: "Service" },
        { key: "booking_type", label: "Booking For", render: (r) => {
          const value = String(r.booking_type || "").toLowerCase();
          const label = value === "individual" ? "Individual" : value === "business" ? "NTAXCO for Business" : "Not specified";
          return <span className={`inline-flex rounded-full px-2 py-1 text-xs font-medium ${value === "individual" ? "bg-blue-50 text-blue-700" : value === "business" ? "bg-violet-50 text-violet-700" : "bg-zinc-100 text-zinc-500"}`}>{label}</span>;
        } },
        { key: "assigned_employee", label: "Employee" },
        { key: "assigned_agent", label: "Agent" },
        { key: "booking_date", label: "Booked" },
        { key: "due_date", label: "Due" },
        { key: "estimated_fee", label: "Amount", render: (r) => (r.estimated_fee ? inr(r.estimated_fee) : "—"), exportValue: (r) => r.estimated_fee },
        { key: "priority", label: "Priority", render: (r) => <PriorityBadge value={r.priority} /> },
        { key: "status", label: "Status", render: (r) => <StatusBadge value={r.status} /> },
        { key: "payment_status", label: "Payment", render: (r) => <StatusBadge value={r.payment_status} /> },
      ]}
      fields={[
        { key: "booking_no", label: "Booking No", required: true },
        { key: "customer_id", label: "Customer", type: "relation", resource: "customers", required: true },
        { key: "service_id", label: "Service", type: "relation", resource: "services", required: true },
        { key: "booking_type", label: "Booking For", type: "select", options: ["individual", "business"] },
        { key: "company", label: "Company Name" },
        { key: "project_name", label: "Project / Business Name" },
        { key: "gst_number", label: "GSTIN" },
        { key: "pan", label: "PAN" },
        { key: "assigned_employee", label: "Assigned Employee" },
        { key: "agent_id", label: "Assigned Agent", type: "relation", resource: "agents" },
        { key: "booking_date", label: "Booking Date", type: "date" },
        { key: "due_date", label: "Due Date", type: "date" },
        { key: "estimated_fee", label: "Amount (₹)", type: "number" },
        { key: "priority", label: "Priority", type: "select", options: PRIORITY, default: "Medium" },
        { key: "status", label: "Status", type: "select", options: STATUS, default: "Pending" },
        { key: "payment_status", label: "Payment Status", type: "select", options: PAY, default: "Pending" },
      ]}
    detailFields={["id", "customer", "customer_id", "service", "booking_type", "company", "project_name", "gst_number", "pan", "status", "priority", "payment_status", "assigned_agent", "assigned_employee", "booking_date", "created_at", "due_date", "estimated_fee", "contact_person", "mobile", "email", "notes"]}
    relationshipEntity="booking"
    />
    {chat && <BookingChat booking={chat} open={!!chat} onOpenChange={(o) => !o && setChat(null)} />}
    <TaskCreateDialog
      open={!!taskBooking}
      onOpenChange={(o) => !o && setTaskBooking(null)}
      initialBooking={taskBooking}
      onCreated={() => window.dispatchEvent(new CustomEvent("ntaxco:data-changed", { detail: { resources: ["tasks"] } }))}
    />
    </>
  );
}
