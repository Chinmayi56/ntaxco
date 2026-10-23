import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AlertTriangle, Loader2, LogIn, ShieldCheck, UserPlus } from "lucide-react";
import api from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import Logo from "@/components/shared/Logo";
import { Button } from "@/components/ui/button";
import { partnerErrorMessage, partnerLoginUrl } from "@/lib/partnerShare";

// Entry point for a "Share with Partner" link (/partner/booking/<token>).
//
// 1. The link is checked (GET /public/partner-share/<token>) - this returns NO booking data.
// 2. The partner must sign in or register with their OWN account (login page, then back here).
// 3. The authenticated partner redeems the token (POST /partner/shares/redeem). The backend verifies the link
//    and creates an access grant for that account only; then we open the booking from "Shared with me".
// All authorization happens in FastAPI - this page only drives the flow.
export default function PartnerBookingView() {
  const { token } = useParams();
  const navigate = useNavigate();
  const { user, loading: authLoading, logout } = useAuth();
  const linkPath = `/partner/booking/${token}`;
  const [link, setLink] = useState({ loading: true, restricted: false, error: "" });
  const [redeem, setRedeem] = useState({ busy: false, error: "" });
  const redeemedFor = useRef(null);

  // Step 1: is the link even live?
  useEffect(() => {
    let active = true;
    setLink({ loading: true, restricted: false, error: "" });
    api.get(`/public/partner-share/${encodeURIComponent(token)}`)
      .then((r) => active && setLink({ loading: false, restricted: !!r.data?.data?.restricted, error: "" }))
      .catch((e) => active && setLink({ loading: false, restricted: false, error: partnerErrorMessage(e) }));
    return () => { active = false; };
  }, [token]);

  // Step 3: once signed in, verify the link on the backend and open the shared booking.
  useEffect(() => {
    if (authLoading || link.loading || link.error || !user?.id) return;
    const key = `${user.id}:${token}`;
    if (redeemedFor.current === key) return;
    redeemedFor.current = key;
    let active = true;
    setRedeem({ busy: true, error: "" });
    api.post("/partner/shares/redeem", { token })
      .then((r) => {
        if (!active) return;
        const d = r.data?.data || {};
        if (d.is_owner) navigate("/customer/bookings", { replace: true });
        else navigate(`/customer/shared-bookings/${encodeURIComponent(d.access_id)}`, { replace: true });
      })
      .catch((e) => active && setRedeem({ busy: false, error: partnerErrorMessage(e) }));
    return () => { active = false; };
  }, [authLoading, link.loading, link.error, user?.id, token, navigate]);

  const switchAccount = async () => {
    await logout();
    navigate(partnerLoginUrl(linkPath), { replace: true });
  };

  const waiting = authLoading || link.loading || redeem.busy;
  const error = link.error || redeem.error;
  const needsLogin = !waiting && !error && !user?.id;

  return (
    <div className="min-h-screen bg-background flex flex-col items-center px-6 py-12" data-testid="partner-booking-view">
      <Logo className="h-10 mb-8" />
      <div className="w-full max-w-md rounded-2xl border border-zinc-200 bg-white p-6 shadow-sm">
        {waiting ? (
          <div className="py-10 flex flex-col items-center gap-3 text-zinc-500" data-testid="partner-view-loading">
            <Loader2 className="h-6 w-6 animate-spin" />
            <p className="text-sm">{redeem.busy ? "Verifying your access…" : "Checking this link…"}</p>
          </div>
        ) : error ? (
          <div className="py-4 text-center" data-testid="partner-view-error">
            <div className="h-12 w-12 rounded-full bg-amber-50 flex items-center justify-center mx-auto mb-3"><AlertTriangle className="h-6 w-6 text-amber-600" /></div>
            <p className="text-sm text-zinc-700">{error}</p>
            {user?.id && !link.error && (
              <div className="mt-5 flex flex-col gap-2">
                <Button type="button" variant="outline" onClick={switchAccount} data-testid="partner-switch-account">Sign in with a different account</Button>
                <Button asChild variant="ghost"><Link to="/customer/shared-bookings">Go to bookings shared with me</Link></Button>
              </div>
            )}
          </div>
        ) : needsLogin ? (
          <div className="text-center" data-testid="partner-login-gate">
            <div className="h-12 w-12 rounded-full bg-brand-faint flex items-center justify-center mx-auto mb-3"><ShieldCheck className="h-6 w-6 text-brand-hover" /></div>
            <h1 className="font-heading text-lg font-bold text-[#0A2540]">A booking was shared with you</h1>
            <p className="text-sm text-muted-foreground mt-2">
              To protect the customer's information, please sign in or create your own NTAXCO account to view it.
              {link.restricted && " Use the email address this link was sent to."}
            </p>
            <div className="mt-5 flex flex-col gap-2">
              <Button asChild className="bg-royal text-white hover:bg-royal-hover font-semibold">
                <Link to={partnerLoginUrl(linkPath)} data-testid="partner-login"><LogIn className="h-4 w-4 mr-1.5" />Sign in</Link>
              </Button>
              <Button asChild variant="outline" className="border-zinc-300">
                <Link to={partnerLoginUrl(linkPath, "signup")} data-testid="partner-register"><UserPlus className="h-4 w-4 mr-1.5" />Create an account</Link>
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
