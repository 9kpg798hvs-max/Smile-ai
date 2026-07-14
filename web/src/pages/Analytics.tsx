import { useEffect, useState } from "react";
import { api } from "../api";

interface Doctor { id: string; display_name: string }
interface Office { id: string; name: string }
interface Summary {
  window_days: number;
  follow_ups: number;
  delivery_rate: number | null;
  response_rate: number | null;
  avg_response_minutes: number | null;
  urgent_cases: number;
  reply_categories_pct: Record<string, number>;
  awaiting_response: number;
}

const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);

export default function Analytics() {
  const [days, setDays] = useState(30);
  const [doctorId, setDoctorId] = useState("");
  const [officeId, setOfficeId] = useState("");
  const [procedure, setProcedure] = useState("");
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [offices, setOffices] = useState<Office[]>([]);
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<Doctor[]>("/doctors").then(setDoctors);
    api.get<Office[]>("/offices").then(setOffices);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams({ days: String(days) });
    if (doctorId) params.set("doctor_id", doctorId);
    if (officeId) params.set("office_id", officeId);
    if (procedure) params.set("procedure", procedure);
    api.get<Summary>(`/analytics/summary?${params}`)
      .then(setData)
      .catch((e) => setError(String(e.message ?? e)));
  }, [days, doctorId, officeId, procedure]);

  if (error) return <div className="error">{error}</div>;

  const categories = data ? Object.entries(data.reply_categories_pct) : [];
  return (
    <>
      <h1>Analytics</h1>
      <div className="row" style={{ marginBottom: 16 }}>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
        <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)}>
          <option value="">All doctors</option>
          {doctors.map((d) => <option key={d.id} value={d.id}>{d.display_name}</option>)}
        </select>
        <select value={officeId} onChange={(e) => setOfficeId(e.target.value)}>
          <option value="">All offices</option>
          {offices.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>
        <input placeholder="Procedure contains…" value={procedure}
               onChange={(e) => setProcedure(e.target.value)} />
      </div>

      {data && (
        <>
          <div className="tile-grid" style={{ marginBottom: 20 }}>
            <div className="card tile"><div className="label">Follow-ups</div><div className="value">{data.follow_ups}</div></div>
            <div className="card tile"><div className="label">Delivery rate</div><div className="value">{pct(data.delivery_rate)}</div></div>
            <div className="card tile"><div className="label">Response rate</div><div className="value">{pct(data.response_rate)}</div></div>
            <div className="card tile"><div className="label">Avg response time</div><div className="value">
              {data.avg_response_minutes === null ? "—" : `${Math.round(data.avg_response_minutes)}m`}
            </div></div>
            <div className="card tile status-critical"><div className="label">Urgent cases</div><div className="value">{data.urgent_cases}</div></div>
            <div className="card tile status-warning"><div className="label">Awaiting response</div><div className="value">{data.awaiting_response}</div></div>
          </div>

          <h2>Reply categories</h2>
          <div className="card">
            {categories.length === 0 && <div className="muted">No classified replies in this window.</div>}
            {/* Single measure (share of replies) → single-hue bars + visible
                value labels; the category name is text, never color-coded. */}
            <table>
              <tbody>
                {categories.map(([name, value]) => (
                  <tr key={name}>
                    <td style={{ width: 200 }}>{name.replaceAll("_", " ")}</td>
                    <td>
                      <div style={{
                        background: "var(--accent)", opacity: 0.85, height: 14,
                        borderRadius: 4, width: `${Math.max(2, value)}%`,
                      }} />
                    </td>
                    <td style={{ width: 60, textAlign: "right" }}>{value}%</td>
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
