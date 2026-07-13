import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { DashboardData, api } from "../api";

/* Tile contract per dataviz spec: label (sentence case) + semibold value.
   Status tiles carry a colored edge AND the label text — never color alone. */
function Tile({
  label, value, to, status,
}: { label: string; value: number; to: string; status?: "critical" | "warning" | "good" }) {
  return (
    <Link className={`card tile${status ? ` status-${status}` : ""}`} to={to}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </Link>
  );
}

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");

  const load = () => api.get<DashboardData>("/dashboard").then(setData).catch((e) => setError(String(e.message ?? e)));
  useEffect(() => {
    load();
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, []);

  if (error) return <div className="error">{error}</div>;
  if (!data) return null;

  return (
    <>
      <h1>Today — {data.date}</h1>
      <div className="hero">
        <span className="value">{data.todays_follow_ups}</span>
        <span className="label">follow-ups today</span>
      </div>

      <h2>Sending pipeline</h2>
      <div className="tile-grid">
        <Tile label="Pending doctor approval" value={data.pending_doctor_approval} to="/approvals" />
        <Tile label="Ready to send" value={data.ready_to_send} to="/inbox" />
        <Tile label="Sent" value={data.sent} to="/inbox" />
        <Tile label="Delivered" value={data.delivered} to="/inbox" />
        <Tile label="Awaiting reply" value={data.awaiting_reply} to="/inbox" />
        <Tile label="Replied" value={data.replied} to="/inbox" />
      </div>

      <h2>Patient replies</h2>
      <div className="tile-grid">
        <Tile label="Needs attention" value={data.needs_attention} to="/inbox?urgency=red" status="critical" />
        <Tile label="Urgent replies" value={data.urgent_replies} to="/inbox?urgency=red" status="critical" />
        <Tile label="Unread messages" value={data.unread_messages} to="/inbox?unread=1" status="warning" />
        <Tile label="Pain" value={data.pain_replies} to="/inbox?category=pain" status="warning" />
        <Tile label="Swelling" value={data.swelling_replies} to="/inbox?category=swelling" status="critical" />
        <Tile label="Medication questions" value={data.medication_questions} to="/inbox?category=medication_question" status="warning" />
        <Tile label="Doing well" value={data.patients_doing_well} to="/inbox?category=doing_well" status="good" />
      </div>
    </>
  );
}
