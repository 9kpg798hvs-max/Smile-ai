import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ConversationSummary, api } from "../api";

const CATEGORIES = [
  "doing_well", "pain", "swelling", "medication_question", "emergency",
  "appointment_request", "question_or_mild_concern", "other",
];

function label(s: string) {
  return s.replaceAll("_", " ");
}

export default function Inbox() {
  const [params, setParams] = useSearchParams();
  const [convs, setConvs] = useState<ConversationSummary[]>([]);
  const [q, setQ] = useState(params.get("q") ?? "");

  const urgency = params.get("urgency") ?? "";
  const category = params.get("category") ?? "";
  const unread = params.get("unread") === "1";
  const archived = params.get("archived") === "1";

  useEffect(() => {
    const search = new URLSearchParams();
    if (urgency) search.set("urgency", urgency);
    if (category) search.set("category", category);
    if (unread) search.set("unread_only", "true");
    if (archived) search.set("archived", "true");
    if (q) search.set("q", q);
    api.get<ConversationSummary[]>(`/conversations?${search}`).then(setConvs);
  }, [urgency, category, unread, archived, q]);

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const order = { red: 0, yellow: 1, green: 2 } as const;
  const sorted = [...convs].sort((a, b) => {
    const ua = a.urgency ? order[a.urgency] : 3;
    const ub = b.urgency ? order[b.urgency] : 3;
    return ua - ub;
  });

  return (
    <>
      <h1>Inbox</h1>
      <div className="row" style={{ marginBottom: 14 }}>
        <input placeholder="Search patient…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={urgency} onChange={(e) => setFilter("urgency", e.target.value)}>
          <option value="">Any urgency</option>
          <option value="red">Red — urgent</option>
          <option value="yellow">Yellow — review</option>
          <option value="green">Green — well</option>
        </select>
        <select value={category} onChange={(e) => setFilter("category", e.target.value)}>
          <option value="">Any category</option>
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>{label(c)}</option>
          ))}
        </select>
        <label className="chip" style={{ cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={unread}
            onChange={(e) => setFilter("unread", e.target.checked ? "1" : "")}
          />{" "}
          unread only
        </label>
        <label className="chip" style={{ cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={archived}
            onChange={(e) => setFilter("archived", e.target.checked ? "1" : "")}
          />{" "}
          archived
        </label>
      </div>

      <div className="card" style={{ padding: 0 }}>
        {sorted.length === 0 && <div className="muted" style={{ padding: 16 }}>No conversations match.</div>}
        {sorted.map((c) => (
          <Link key={c.id} to={`/inbox/${c.id}`} className="conv-row">
            {c.unread_count > 0 ? <span className="unread-dot" /> : <span style={{ width: 8 }} />}
            <span className="who">{c.patient_name}</span>
            {c.urgency && <span className={`badge ${c.urgency}`}>{c.urgency}</span>}
            {c.category && <span className="chip">{label(c.category)}</span>}
            {c.needs_manual_review && <span className="badge yellow">needs review</span>}
            <span className="preview">{c.last_message_preview ?? "—"}</span>
          </Link>
        ))}
      </div>
    </>
  );
}
