// Helpers for the partner login gate. Kept free of React so they can be unit-tested with plain node.

const PARTNER_LINK_PATH = /^\/partner\/booking\/[A-Za-z0-9_-]{32,64}$/;

// Post-login return path: ONLY a partner invite link is accepted (prevents open redirects).
export function safePartnerNextPath(value) {
  const v = String(value ?? "");
  return PARTNER_LINK_PATH.test(v) ? v : null;
}

export function partnerLoginUrl(linkPath, mode = "signin") {
  const params = new URLSearchParams();
  if (mode === "signup") params.set("mode", "signup");
  params.set("next", linkPath);
  return `/login?${params.toString()}`;
}

export function partnerErrorMessage(error) {
  const status = error?.response?.status;
  const detail = error?.response?.data?.detail;
  if (status === 410) return "This link has expired or was withdrawn by the customer.";
  if (status === 404) return "This link is not valid.";
  if (status === 429) return "Too many requests. Please try again in a minute.";
  if (status === 403 && typeof detail === "string") return detail;
  return "We could not open this shared booking right now. Please try again shortly.";
}

// ---- Shared Bookings dashboard helpers (pure, unit-tested with plain node) --------------------------------------

export const LINK_EXPIRY_OPTIONS = [
  { days: 1, label: "24 hours" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

export const ACCESS_PERIOD_OPTIONS = [
  { days: "", label: "Until I remove it" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

export function formatShareDate(value) {
  if (!value) return "";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
}

export function formatShareDateTime(value) {
  if (!value) return "";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

// "expires in 3 days" / "expired" - used for both invite links and time-limited partner access.
export function expiryLabel(value, now = Date.now()) {
  if (!value) return "";
  const end = new Date(value).getTime();
  if (Number.isNaN(end)) return "";
  const ms = end - now;
  if (ms <= 0) return "expired";
  const hours = Math.ceil(ms / 3600000);
  if (hours < 24) return `expires in ${hours} hour${hours === 1 ? "" : "s"}`;
  const days = Math.ceil(hours / 24);
  return `expires in ${days} day${days === 1 ? "" : "s"}`;
}

// Only data: URIs for the document types the backend accepts are ever turned into a download.
const SAFE_DOC_MIME = /^data:(application\/pdf|image\/(jpeg|png|webp)|application\/msword|application\/vnd\.openxmlformats-officedocument\.(wordprocessingml\.document|spreadsheetml\.sheet));base64,/;

export function isSafeDocumentDataUri(value) {
  return typeof value === "string" && SAFE_DOC_MIME.test(value.slice(0, 160));
}

export function safeDownloadName(name, fallback = "document") {
  const cleaned = String(name || "").replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "_").replace(/^\.+/, "").trim().slice(0, 120);
  return cleaned || fallback;
}

export function downloadDataUri(fileName, dataUri) {
  if (!isSafeDocumentDataUri(dataUri)) throw new Error("This file type cannot be downloaded.");
  const link = document.createElement("a");
  link.href = dataUri;
  link.download = safeDownloadName(fileName);
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}
