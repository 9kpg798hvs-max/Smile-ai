import { useEffect, useState } from "react";
import { useMe } from "../App";
import { ApiError, api } from "../api";

interface Doctor { id: string; display_name: string }
interface DoctorSettings {
  doctor_id: string;
  display_name: string;
  send_time: string;
  tone: string;
  signature: string | null;
  wider_access: boolean;
}

export default function Settings() {
  const me = useMe();
  const isAdmin = me?.role === "practice_admin" || me?.role === "super_admin";
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [doctorId, setDoctorId] = useState("");
  const [settings, setSettings] = useState<DoctorSettings | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api.get<Doctor[]>("/doctors").then((list) => {
      setDoctors(list);
      const initial = me?.role === "doctor" ? me.id : list[0]?.id;
      if (initial) setDoctorId(initial);
    });
  }, []);

  useEffect(() => {
    if (!doctorId) return;
    setMessage("");
    setError("");
    api.get<DoctorSettings>(`/doctors/${doctorId}/settings`)
      .then(setSettings)
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [doctorId]);

  const save = async () => {
    if (!settings) return;
    setError("");
    try {
      const patch: Record<string, unknown> = {
        display_name: settings.display_name,
        send_time: settings.send_time,
        tone: settings.tone,
        signature: settings.signature,
      };
      if (isAdmin) patch.wider_access = settings.wider_access;
      const result = await api.patch<{ rescheduled_pending_follow_ups: number }>(
        `/doctors/${doctorId}/settings`, patch,
      );
      setMessage(
        result.rescheduled_pending_follow_ups > 0
          ? `Saved — ${result.rescheduled_pending_follow_ups} pending message(s) moved to the new send time.`
          : "Saved.",
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "save failed");
    }
  };

  const editable = me?.role !== "doctor" || doctorId === me?.id;

  return (
    <>
      <h1>Doctor settings</h1>
      <div className="row" style={{ marginBottom: 14 }}>
        <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)}>
          {doctors.map((d) => <option key={d.id} value={d.id}>{d.display_name}</option>)}
        </select>
        {!editable && <span className="muted">view only — doctors edit their own settings</span>}
      </div>
      {error && <div className="error" style={{ marginBottom: 10 }}>{error}</div>}
      {message && <div className="card" style={{ marginBottom: 10 }}>{message}</div>}

      {settings && (
        <div className="card" style={{ maxWidth: 520 }}>
          <div className="row" style={{ flexDirection: "column", alignItems: "stretch" }}>
            <label className="muted">Display name (how patients see you)</label>
            <input value={settings.display_name} disabled={!editable}
                   onChange={(e) => setSettings({ ...settings, display_name: e.target.value })} />
            <label className="muted">Daily send time (office local time)</label>
            <input type="time" value={settings.send_time} disabled={!editable}
                   onChange={(e) => setSettings({ ...settings, send_time: e.target.value })} />
            <label className="muted">Tone</label>
            <select value={settings.tone} disabled={!editable}
                    onChange={(e) => setSettings({ ...settings, tone: e.target.value })}>
              <option value="friendly">Friendly</option>
              <option value="formal">Formal</option>
              <option value="custom">Custom</option>
            </select>
            <label className="muted">Signature (optional)</label>
            <input value={settings.signature ?? ""} disabled={!editable}
                   onChange={(e) => setSettings({ ...settings, signature: e.target.value })} />
            {isAdmin && (
              <label className="chip" style={{ cursor: "pointer" }}>
                <input type="checkbox" checked={settings.wider_access}
                       onChange={(e) => setSettings({ ...settings, wider_access: e.target.checked })} />
                {" "}can see all doctors' patients (admin-granted)
              </label>
            )}
            <button className="btn primary" disabled={!editable} onClick={save}>Save</button>
          </div>
        </div>
      )}
    </>
  );
}
