// Thin API client. Session cookie is httpOnly; every call is same-origin.

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/v1${path}`, {
    credentials: "same-origin",
    headers: init?.body instanceof FormData
      ? undefined
      : { "Content-Type": "application/json" },
    ...init,
  });
  if (res.status === 401 && !path.startsWith("/auth/")) {
    window.location.href = "/login";
    throw new ApiError(401, "not authenticated");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* keep statusText */ }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  postForm: <T>(path: string, form: FormData) =>
    request<T>(path, { method: "POST", body: form }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

export interface Me {
  id: string;
  email: string;
  full_name: string;
  role: string;
  permissions: string[];
}

export interface DashboardData {
  date: string;
  todays_follow_ups: number;
  pending_doctor_approval: number;
  ready_to_send: number;
  sent: number;
  delivered: number;
  awaiting_reply: number;
  replied: number;
  needs_attention: number;
  unread_messages: number;
  urgent_replies: number;
  pain_replies: number;
  swelling_replies: number;
  medication_questions: number;
  patients_doing_well: number;
}

export interface ConversationSummary {
  id: string;
  patient_name: string;
  doctor_id: string;
  unread_count: number;
  last_message_at: string | null;
  last_message_preview: string | null;
  category: string | null;
  urgency: "red" | "yellow" | "green" | null;
  needs_manual_review: boolean;
}

export interface ThreadMessage {
  id: string;
  direction: "in" | "out";
  body: string;
  status: string;
  created_at: string;
  approved_by: string | null;
  ai?: { category: string; override_category: string | null; urgency: string; confidence: number | null };
  draft?: { id: string; body: string; status: string };
}

export interface Thread {
  id: string;
  patient: { id: string; name: string; mobile: string; sms_opt_out: boolean };
  status: string;
  messages: ThreadMessage[];
  internal_notes: { id: string; body: string; created_at: string }[];
}

export interface PendingApproval {
  upload_id: string;
  schedule_date: string | null;
  patients: {
    entry_id: string;
    patient_name: string;
    time: string;
    procedure: string;
    phone: string;
    message_preview: string;
  }[];
}

export interface UploadEntry {
  id: string;
  patient_name: string;
  time: string;
  doctor_name: string;
  procedure: string;
  phone: string;
  ocr_crossed_out: boolean;
  ocr_confidence: Record<string, number>;
  excluded: boolean;
  exclusion_source: string | null;
  resolved_doctor_id: string | null;
}

export interface Upload {
  id: string;
  status: string;
  schedule_date: string | null;
  ocr_model: string;
  entries: UploadEntry[];
}
