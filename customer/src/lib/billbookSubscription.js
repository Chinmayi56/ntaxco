import api, { formatApiError } from "@/lib/api";
import { parsePlansResponse } from "@/lib/billbookPlans";

// All prices, plan validity, ownership and payment status come from the backend. The browser only
// sends the selected plan id and the customer's details; it never sends a price or a status.
const DRAFT_KEY = "billbook_subscription_draft"; // form values only - never payment/subscription state

export async function fetchPlans() {
  const { data } = await api.get("/billbook/plans");
  return parsePlansResponse(data); // throws on an unexpected body so the page shows an error, not "No plans"
}
export async function fetchSubscriptionStatus() {
  const { data } = await api.get("/billbook/subscription/status");
  return data.data;
}
export async function createGuestCheckout(payload) {
  const { data } = await api.post("/billbook/guest-checkout", payload);
  return data.data;
}
export async function verifyGuestCheckout(payload) {
  const { data } = await api.post("/billbook/guest-checkout/verify", payload);
  return data.data;
}
export async function claimGuestCheckout(payload) {
  const { data } = await api.post("/billbook/guest-checkout/claim", payload);
  return data.data;
}
export async function createCheckout(payload) {
  const { data } = await api.post("/billbook/subscription/checkout", payload);
  return data.data;
}
export async function verifySubscriptionPayment(response) {
  const { data } = await api.post("/billbook/subscription/verify", {
    razorpay_order_id: response.razorpay_order_id,
    razorpay_payment_id: response.razorpay_payment_id,
    razorpay_signature: response.razorpay_signature,
  });
  return data.data;
}
export async function fetchOrderStatus(orderId) {
  const { data } = await api.get(`/billbook/subscription/order/${encodeURIComponent(orderId)}`);
  return data.data;
}
export async function cancelCheckoutAttempt(orderId) {
  const { data } = await api.post("/billbook/subscription/cancel-attempt", { order_id: orderId });
  return data.data;
}

export function apiMessage(error, fallback) {
  return formatApiError(error?.response?.data?.detail) || error?.message || fallback;
}
export function apiFieldErrors(error) {
  const detail = error?.response?.data?.detail;
  return detail && typeof detail === "object" && detail.fields ? detail.fields : null;
}
const STATUS_MESSAGES = {
  400: "We could not process that request. Please check your details and try again.",
  401: "Your session has expired. Please sign in again to continue.",
  403: "This account is not allowed to perform this action.",
  409: "This request conflicts with your current subscription. Please refresh and check your subscription status.",
  422: "Please correct the highlighted fields and try again.",
  429: "Too many attempts. Please wait a moment and try again.",
  500: "We could not start your payment because of a problem on our side. You were not charged. Please try again shortly.",
  502: "The payment service is not responding right now. You were not charged. Please try again shortly.",
  503: "Online payments are temporarily unavailable. You were not charged. Please try again later.",
  504: "The server took too long to respond. Please check your subscription status before trying again.",
};

// Safe, meaningful message for any API failure: the backend's own message when it sent one (never raw
// database text), otherwise a message that matches the HTTP status. Server errors carry the support reference.
export function apiErrorMessage(error, fallback) {
  const status = error?.response?.status;
  if (!error?.response) {
    return error?.code === "ECONNABORTED"
      ? "The server took too long to respond. Please check your subscription status before trying again."
      : "Cannot reach the server. Check your internet connection and try again.";
  }
  const detail = error.response.data?.detail;
  let text = "";
  if (detail && typeof detail === "object" && !Array.isArray(detail) && detail.message) text = detail.message;
  else if (typeof detail === "string" && detail.trim()) text = detail;
  else if (Array.isArray(detail) && detail.length) text = apiMessage(error, "");
  if (!text) text = STATUS_MESSAGES[status] || fallback || "Something went wrong. Please try again.";
  const ref = detail && typeof detail === "object" ? detail.correlation_id : "";
  return status >= 500 && ref ? `${text} (Reference: ${ref})` : text;
}

// A checkout order is usable only if the backend returned everything Razorpay Checkout needs.
export function isValidCheckoutOrder(order) {
  return !!order && typeof order.order_id === "string" && order.order_id.startsWith("order_")
    && typeof order.key_id === "string" && order.key_id.length > 0
    && Number.isInteger(order.amount) && order.amount > 0 && order.currency === "INR";
}

export function loadDraft() {
  try { return JSON.parse(sessionStorage.getItem(DRAFT_KEY) || "null") || {}; } catch { return {}; }
}
export function saveDraft(values) {
  try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify(values)); } catch { /* storage unavailable */ }
}
export function clearDraft() {
  try { sessionStorage.removeItem(DRAFT_KEY); } catch { /* ignore */ }
}

// Opens the existing Razorpay Checkout (checkout.js is already loaded by public/index.html).
// A popup callback is NOT proof of payment: the caller must send the response to the backend verify API.
export function openRazorpayCheckout(order, { onSuccess, onDismiss, onFailure }) {
  if (typeof window === "undefined" || !window.Razorpay) {
    throw new Error("Razorpay Checkout could not be loaded. Check your connection and try again.");
  }
  const rzp = new window.Razorpay({
    key: order.key_id,
    order_id: order.order_id,
    amount: order.amount,
    currency: order.currency,
    name: "NTAXCO - My BillBook",
    description: `My BillBook subscription - ${order.plan?.name || ""}`,
    prefill: order.prefill,
    theme: { color: "#0B5ED7" },
    handler: onSuccess,
    modal: { ondismiss: onDismiss },
  });
  rzp.on("payment.failed", (res) => onFailure?.(res?.error?.description || "Payment failed."));
  rzp.open();
}
