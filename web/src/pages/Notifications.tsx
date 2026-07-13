import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

interface Note {
  id: string;
  type: string;
  title: string;
  payload: Record<string, unknown>;
  read: boolean;
  created_at: string;
}

const URGENT_TYPES = new Set(["urgent_reply", "high_priority_symptom", "message_failed"]);

export default function Notifications() {
  const [notes, setNotes] = useState<Note[]>([]);

  const load = () => api.get<Note[]>("/notifications").then(setNotes);
  useEffect(() => {
    load();
  }, []);

  const markAll = async () => {
    await api.post("/notifications/read-all");
    await load();
  };

  return (
    <>
      <h1>Notifications</h1>
      <div className="row" style={{ marginBottom: 12 }}>
        <button className="btn" onClick={markAll}>Mark all read</button>
      </div>
      <div className="card" style={{ padding: 0 }}>
        {notes.length === 0 && <div className="muted" style={{ padding: 16 }}>Nothing yet.</div>}
        {notes.map((n) => (
          <div key={n.id} className="conv-row" style={{ opacity: n.read ? 0.6 : 1 }}>
            {!n.read ? <span className="unread-dot" /> : <span style={{ width: 8 }} />}
            {URGENT_TYPES.has(n.type)
              ? <span className="badge red">urgent</span>
              : <span className="badge neutral">{n.type.replaceAll("_", " ")}</span>}
            <span style={{ flex: 1 }}>
              {n.title}
              {typeof n.payload.conversation_id === "string" && (
                <> — <Link to={`/inbox/${n.payload.conversation_id}`}>open thread</Link></>
              )}
            </span>
            <span className="muted">{new Date(n.created_at).toLocaleString()}</span>
            {!n.read && (
              <button className="btn" onClick={() => api.post(`/notifications/${n.id}/read`).then(load)}>
                Read
              </button>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
