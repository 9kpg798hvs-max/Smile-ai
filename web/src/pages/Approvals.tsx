import { useEffect, useState } from "react";
import { ApiError, PendingApproval, api } from "../api";

export default function Approvals() {
  const [pending, setPending] = useState<PendingApproval[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = () => api.get<PendingApproval[]>("/approvals/pending").then(setPending);
  useEffect(() => {
    load().catch((e) => setError(String(e.message ?? e)));
  }, []);

  const approve = async (uploadId: string) => {
    setError("");
    try {
      const result = await api.post<{
        follow_ups_created: number;
        duplicates_excluded: number;
        opted_out: number;
      }>(`/schedule-uploads/${uploadId}/approve`);
      setMessage(
        `Approved: ${result.follow_ups_created} message(s) scheduled` +
        (result.duplicates_excluded ? `, ${result.duplicates_excluded} duplicate(s) skipped` : "") +
        (result.opted_out ? `, ${result.opted_out} opted-out patient(s) skipped` : ""),
      );
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "approve failed");
    }
  };

  const reject = async (uploadId: string) => {
    const note = window.prompt("What should the staff fix?");
    if (!note) return;
    await api.post(`/schedule-uploads/${uploadId}/reject`, { note });
    setMessage("Returned to staff for correction.");
    await load();
  };

  return (
    <>
      <h1>Awaiting your approval</h1>
      {message && <div className="card" style={{ marginBottom: 12 }}>{message}</div>}
      {error && <div className="error">{error}</div>}
      {pending.length === 0 && <div className="muted">Nothing waiting — you're all caught up.</div>}

      {pending.map((u) => (
        <div key={u.upload_id} className="card" style={{ marginBottom: 16 }}>
          <h2 style={{ marginTop: 0 }}>
            Schedule for {u.schedule_date ?? "today"} · {u.patients.length} patient(s)
          </h2>
          <table>
            <thead>
              <tr>
                <th>Time</th><th>Patient</th><th>Procedure</th><th>Phone</th>
                <th>Message that will be sent</th>
              </tr>
            </thead>
            <tbody>
              {u.patients.map((p) => (
                <tr key={p.entry_id}>
                  <td>{p.time}</td>
                  <td><strong>{p.patient_name}</strong></td>
                  <td>{p.procedure}</td>
                  <td>{p.phone}</td>
                  <td className="muted">{p.message_preview}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn primary" onClick={() => approve(u.upload_id)}>
              Approve — schedule these messages
            </button>
            <button className="btn danger" onClick={() => reject(u.upload_id)}>
              Reject with note
            </button>
          </div>
        </div>
      ))}
    </>
  );
}
