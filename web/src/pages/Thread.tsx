import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useMe } from "../App";
import { ApiError, Thread as ThreadData, ThreadMessage, api } from "../api";

const CATEGORIES = [
  "doing_well", "pain", "swelling", "medication_question", "emergency",
  "appointment_request", "question_or_mild_concern", "other",
];

export default function Thread() {
  const { conversationId } = useParams();
  const me = useMe();
  const canSend = me?.permissions.includes("send_reply") ?? false;
  const [thread, setThread] = useState<ThreadData | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  const load = () =>
    api.get<ThreadData>(`/conversations/${conversationId}`).then((t) => {
      setThread(t);
      return api.post(`/conversations/${conversationId}/read`);
    });

  useEffect(() => {
    load().catch((e) => setError(String(e.message ?? e)));
  }, [conversationId]);

  if (error) return <div className="error">{error}</div>;
  if (!thread) return null;

  const addNote = async () => {
    await api.post(`/conversations/${thread.id}/notes`, { body: note });
    setNote("");
    await load();
  };

  return (
    <>
      <h1>
        {thread.patient.name}{" "}
        <span className="muted">{thread.patient.mobile}</span>{" "}
        {thread.patient.sms_opt_out && <span className="badge red">opted out</span>}
      </h1>
      <div className="row">
        <button
          className="btn"
          onClick={() => api.post(`/conversations/${thread.id}/archive`).then(load)}
        >
          Archive
        </button>
      </div>

      <div className="thread">
        {thread.messages.map((m) => (
          <Bubble key={m.id} m={m} canSend={canSend} reload={load} />
        ))}
      </div>

      <h2>Internal notes (never sent to the patient)</h2>
      <div className="card">
        {thread.internal_notes.map((n) => (
          <div key={n.id} style={{ marginBottom: 8 }}>
            <span className="muted">{new Date(n.created_at).toLocaleString()}: </span>
            {n.body}
          </div>
        ))}
        <div className="row">
          <input
            style={{ flex: 1 }}
            placeholder="Add a note for the team…"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <button className="btn" disabled={!note.trim()} onClick={addNote}>
            Add note
          </button>
        </div>
      </div>
    </>
  );
}

function Bubble({ m, canSend, reload }: { m: ThreadMessage; canSend: boolean; reload: () => Promise<unknown> }) {
  const [draftBody, setDraftBody] = useState(m.draft?.body ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const overrideCategory = async (category: string) => {
    await api.post(`/replies/${m.id}/override-category`, { category });
    await reload();
  };

  const approveAndSend = async () => {
    if (!m.draft) return;
    setBusy(true);
    setError("");
    try {
      await api.post(`/drafts/${m.draft.id}/approve-and-send`, {
        edited_body: draftBody !== m.draft.body ? draftBody : null,
      });
      await reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "send failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`bubble ${m.direction}`}>
      <div>{m.body}</div>
      <div className="meta">
        {new Date(m.created_at).toLocaleString()}
        {m.direction === "out" && m.approved_by && " · approved & sent by staff on record"}
        {m.direction === "out" && ` · ${m.status}`}
      </div>

      {m.ai && (
        <div className="card ai-panel" style={{ marginTop: 10 }}>
          <div className="row">
            <span className={`badge ${m.ai.urgency}`}>{m.ai.urgency}</span>
            <span className="muted">AI category:</span>
            <select
              value={m.ai.override_category ?? m.ai.category}
              onChange={(e) => overrideCategory(e.target.value)}
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{c.replaceAll("_", " ")}</option>
              ))}
            </select>
            {m.ai.confidence === null && <span className="badge yellow">needs review</span>}
          </div>

          {m.draft && m.draft.status === "suggested" && (
            <div style={{ marginTop: 10 }}>
              <div className="muted" style={{ marginBottom: 4 }}>
                Suggested reply — nothing sends until a person approves it:
              </div>
              <textarea
                rows={3}
                value={draftBody}
                onChange={(e) => setDraftBody(e.target.value)}
              />
              <div className="row" style={{ marginTop: 6 }}>
                <button className="btn primary" disabled={!canSend || busy} onClick={approveAndSend}>
                  Approve &amp; send
                </button>
                {!canSend && <span className="muted">your role can edit but not send</span>}
                {error && <span className="error">{error}</span>}
              </div>
            </div>
          )}
          {m.draft && m.draft.status === "approved" && (
            <div className="muted" style={{ marginTop: 8 }}>Draft approved and sent.</div>
          )}
        </div>
      )}
    </div>
  );
}
