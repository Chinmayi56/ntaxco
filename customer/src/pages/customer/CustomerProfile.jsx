import { useEffect, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import PageHeader from "@/components/shared/PageHeader";
import DataTable from "@/components/shared/DataTable";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { useCustomerPurchases } from "@/hooks/useCustomerPurchases";
import api, { describeApiError } from "@/lib/api";
import { inr } from "@/lib/utils";
import { Building2, Mail, Phone, MapPin, FileText, Hash, User, CalendarCheck, LogIn, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Pencil, Save, X } from "lucide-react";

const fmtDateTime = (v) => {
  if (!v) return null;
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
};

export default function CustomerProfile() {
  const { user, updateCustomerProfile } = useAuth();
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ full_name: "", mobile: "", gstin: "" });
  const [profile, setProfile] = useState(null);
  const { rows: history, loading: historyLoading } = useCustomerPurchases();
  const accountKey = user?.id;

  useEffect(() => {
    // Drop whatever the previous customer left in state before loading this one.
    setProfile(null);
    if (!user) return undefined;
    let active = true;
    const request = api.get("/customer/profile").then(({ data }) => data?.data?.profile || null);
    request
      .then((own) => {
        if (!active) return;
        setProfile(own);
        setForm({
          full_name: own?.owner || user?.name || "",
          mobile: own?.mobile || user?.mobile || "",
          gstin: own?.gst_number || own?.gstin || "",
        });
      })
      .catch((e) => toast.error(describeApiError(e, "Unable to load your profile")));
    return () => { active = false; };
  }, [accountKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveProfile = async () => {
    if (!String(form.full_name || "").trim()) { toast.error("Please enter your name."); return; }
    if (!/^[6-9][0-9]{9}$/.test(String(form.mobile || "").trim())) {
      toast.error("Enter a valid 10-digit Indian mobile number starting with 6-9."); return;
    }
    setSaving(true);
    try {
      const result = await updateCustomerProfile(form);
      setProfile(result?.profile || profile);
      setEditing(false);
      toast.success("Profile updated successfully.");
    } catch (error) {
      toast.error(error.message || "Unable to update profile.");
    } finally { setSaving(false); }
  };

  const p = profile || {};
  const address = [p.address, p.city, p.state, p.pincode].filter(Boolean).join(", ");
  const fields = [
    { icon: User, label: "Customer Name", value: p.owner || user?.name },
    { icon: Building2, label: "Business Name", value: p.business_name },
    { icon: Phone, label: "Mobile", value: p.mobile || user?.mobile },
    { icon: Mail, label: "Email", value: p.email || user?.email },
    { icon: MapPin, label: "Address", value: address },
    { icon: Hash, label: "Customer ID", value: p.cust_id || p.id || user?.meta?.customer_id },
    { icon: ShieldCheck, label: "Account Status", value: p.status },
    { icon: FileText, label: "Onboarding Status", value: p.onboarding_status },
    { icon: FileText, label: "Business Type", value: p.business_type },
    { icon: Hash, label: "GST Number", value: p.gst_number },
    { icon: Hash, label: "PAN", value: p.pan },
    { icon: CalendarCheck, label: "Registration", value: fmtDateTime(p.created_at) || (user?.meta?.registered ? "Self-registered" : null) },
    { icon: LogIn, label: "Last Login", value: fmtDateTime(p.last_login) },
  ];

  const columns = [
    { key: "booking_ref", label: "Booking ID", render: (r) => r.booking_ref || "—" },
    { key: "service", label: "Service", render: (r) => r.service || "—" },
    { key: "booking_date", label: "Booking Date", render: (r) => r.booking_date || "—" },
    { key: "due_date", label: "Due Date", render: (r) => r.due_date || "—" },
    { key: "consultant", label: "Consultant", render: (r) => r.consultant || "—" },
    { key: "amount", label: "Amount", render: (r) => (r.amount == null ? "—" : inr(r.amount)), exportValue: (r) => r.amount },
    { key: "payment_status", label: "Payment", render: (r) => <StatusBadge value={r.payment_status} /> },
    { key: "booking_status", label: "Service Status", render: (r) => (r.booking_status ? <StatusBadge value={r.booking_status} /> : "—") },
    { key: "invoice_no", label: "Invoice", render: (r) => r.invoice_no || <span className="text-zinc-400">Pending</span> },
    { key: "payment_date", label: "Payment Date", render: (r) => r.payment_date || "—" },
  ];

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      <PageHeader title="My Profile" breadcrumb={["Customer", "My Profile"]} subtitle="Your account details and purchase history." />
      <div className="bg-white border border-zinc-200 rounded-2xl shadow-sm overflow-hidden mb-8" data-testid="customer-profile-card">
        <div className="bg-gradient-to-br from-brand-light to-brand-faint p-6 flex items-center gap-4">
          <div className="h-16 w-16 rounded-2xl bg-white shadow-sm flex items-center justify-center text-brand-hover"><User className="h-8 w-8" /></div>
          <div className="flex-1">
            <h2 className="font-heading text-xl font-bold text-zinc-900" data-testid="profile-name">{p.owner || p.business_name || user?.name}</h2>
            <p className="text-sm text-zinc-600">Customer ID: {p.cust_id || p.id || user?.meta?.customer_id || "—"}</p>
          </div>
          {!editing ? <Button variant="outline" onClick={() => setEditing(true)}><Pencil className="h-4 w-4 mr-2" />Edit Profile</Button> : <div className="flex gap-2">
            <Button onClick={saveProfile} disabled={saving}>{saving ? "Saving…" : <><Save className="h-4 w-4 mr-2" />Save</>}</Button>
            <Button variant="outline" onClick={() => { setForm({ full_name: p.owner || user?.name || "", mobile: p.mobile || user?.mobile || "", gstin: p.gst_number || p.gstin || "" }); setEditing(false); }} disabled={saving}><X className="h-4 w-4 mr-2" />Cancel</Button>
          </div>}
        </div>
        {editing && <div className="p-6 grid grid-cols-1 sm:grid-cols-3 gap-4 border-b border-zinc-100">
          <div className="space-y-2"><Label htmlFor="profile-full-name">Full name</Label><Input id="profile-full-name" value={form.full_name} maxLength={120} onChange={(e) => setForm((v) => ({ ...v, full_name: e.target.value }))} /></div>
          <div className="space-y-2"><Label htmlFor="profile-mobile">Mobile (10 digits)</Label><Input id="profile-mobile" inputMode="numeric" maxLength={10} value={form.mobile} onChange={(e) => setForm((v) => ({ ...v, mobile: e.target.value.replace(/\D/g, "").slice(0, 10) }))} /></div>
          <div className="space-y-2"><Label htmlFor="profile-gstin">GSTIN (optional)</Label><Input id="profile-gstin" maxLength={15} value={form.gstin} onChange={(e) => setForm((v) => ({ ...v, gstin: e.target.value.toUpperCase().replace(/[^0-9A-Z]/g, "").slice(0, 15) }))} /></div>
        </div>}
        <div className="p-6 grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-1">
          {fields.map((f) => (
            <div key={f.label} className="flex items-center gap-3 py-3 border-b border-zinc-100">
              <f.icon className="h-4 w-4 text-zinc-400 shrink-0" />
              <span className="text-sm text-muted-foreground w-40">{f.label}</span>
              <span className="text-sm font-medium text-zinc-800 ml-auto text-right">{f.value || "—"}</span>
            </div>
          ))}
        </div>
      </div>
      <h2 className="font-heading text-lg font-bold text-zinc-900 mb-3" data-testid="history-heading">Purchase / Service History</h2>
      <DataTable key={accountKey} title="Purchase / Service History" columns={columns} rows={history} loading={historyLoading} pageSize={8} testId="customer-history-table" />
    </div>
  );
}
