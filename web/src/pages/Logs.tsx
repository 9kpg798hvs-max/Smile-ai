import { useEffect, useState } from "react";
import { useMe } from "../App";
import { api } from "../api";

interface DeliveryRow {
  message_id: string;
  event: string;
  to: string;
  provider: string;
  provider_message_id: string | null;
  occurred_at: string;
  detail: Record<string, unknown>;
}
interface AuditRow {
  id: string;
  action: string;
  user_id: string | null;
  entity_type: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export default function Logs() {
  const me = useMe();
  const canAudit = me?.permissions.includes("view_audit_logs") ?? false;
  const [delivery, setDelivery] = useState<DeliveryRow[]>([]);
  const [auditRows, setAuditRows] = useState<AuditRow[]>([]);
  const [action, setAction] = useState("");

  useEffect(() => {
    api.get<DeliveryRow[]>("/logs/delivery").then(setDelivery);
  }, []);
  useEffect(() => {
    if (!canAudit) return;
    const params = action ? `?action=${encodeURIComponent(action)}` : "";
    api.get<AuditRow[]>(`/audit-log${params}`).then(setAuditRows);
  }, [canAudit, action]);

  return (
    <>
      <h1>Logs</h1>
      <h2>Message delivery</h2>
      <div className="card" style={{ padding: 0, marginBottom: 20 }}>
        <table>
          <thead><tr><th>When</th><th>Event</th><th>To</th><th>Provider</th><th>Detail</th></tr></thead>
          <tbody>
            {delivery.length === 0 && (
              <tr><td colSpan={5} className="muted">No delivery events yet.</td></tr>
            )}
            {delivery.map((d, i) => (
              <tr key={`${d.message_id}-${i}`}>
                <td style={{ fontVariantNumeric: "tabular-nums" }}>
                  {new Date(d.occurred_at).toLocaleString()}
                </td>
                <td>
                  {d.event === "failed" || d.event === "undelivered"
                    ? <span className="badge red">{d.event}</span>
                    : <span className="badge neutral">{d.event}</span>}
                </td>
                <td>{d.to}</td>
                <td>{d.provider}</td>
                <td className="muted">{JSON.stringify(d.detail)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {canAudit && (
        <>
          <h2>Audit trail</h2>
          <div className="row" style={{ marginBottom: 8 }}>
            <input placeholder="Filter by action (e.g. message.sent)" value={action}
                   onChange={(e) => setAction(e.target.value)} />
          </div>
          <div className="card" style={{ padding: 0 }}>
            <table>
              <thead><tr><th>When</th><th>Action</th><th>Entity</th><th>Details</th></tr></thead>
              <tbody>
                {auditRows.map((a) => (
                  <tr key={a.id}>
                    <td style={{ fontVariantNumeric: "tabular-nums" }}>
                      {new Date(a.created_at).toLocaleString()}
                    </td>
                    <td><span className="chip">{a.action}</span></td>
                    <td className="muted">{a.entity_type ?? "—"}</td>
                    <td className="muted">{JSON.stringify(a.details)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
