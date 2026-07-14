import { FormEvent, useEffect, useState } from "react";
import { useMe } from "../App";
import { ApiError, api } from "../api";

interface UserRow { id: string; email: string; full_name: string; role: string; is_active: boolean }
interface Office { id: string; name: string; timezone: string }
interface Phone { id: string; e164: string; doctor_id: string | null; office_id: string | null; provider: string; status: string; mock: boolean }
interface Doctor { id: string; display_name: string }

export default function Admin() {
  const me = useMe();
  const can = (p: string) => me?.permissions.includes(p) ?? false;
  return (
    <>
      <h1>Practice administration</h1>
      {can("manage_users") && <Users />}
      {can("manage_offices") && <Offices />}
      {can("manage_phone_numbers") && <Phones />}
      {can("manage_security") && <PracticeSettings />}
    </>
  );
}

function Users() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState("staff");
  const [displayName, setDisplayName] = useState("");
  const [tempPassword, setTempPassword] = useState("");
  const [error, setError] = useState("");

  const load = () => api.get<UserRow[]>("/users").then(setUsers);
  useEffect(() => { load(); }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setTempPassword("");
    try {
      const result = await api.post<{ temp_password: string | null }>("/users", {
        email, full_name: fullName, role,
        display_name: role === "doctor" ? (displayName || fullName) : undefined,
      });
      if (result.temp_password) setTempPassword(result.temp_password);
      setEmail(""); setFullName(""); setDisplayName("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "create failed");
    }
  };

  const toggleActive = async (u: UserRow) => {
    await api.patch(`/users/${u.id}`, { is_active: !u.is_active });
    await load();
  };

  return (
    <>
      <h2>Team</h2>
      <div className="card" style={{ padding: 0, marginBottom: 10 }}>
        <table>
          <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th /></tr></thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.full_name}</td>
                <td>{u.email}</td>
                <td><span className="chip">{u.role.replaceAll("_", " ")}</span></td>
                <td>{u.is_active ? "active" : <span className="badge neutral">deactivated</span>}</td>
                <td><button className="btn" onClick={() => toggleActive(u)}>
                  {u.is_active ? "Deactivate" : "Reactivate"}
                </button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <form className="card" onSubmit={create} style={{ marginBottom: 20 }}>
        <div className="row">
          <input placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
          <input placeholder="Full name" value={fullName} onChange={(e) => setFullName(e.target.value)} />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="staff">Staff / front desk</option>
            <option value="doctor">Doctor</option>
            <option value="office_manager">Office manager</option>
            <option value="practice_admin">Practice admin</option>
          </select>
          {role === "doctor" && (
            <input placeholder='Display name ("Dr. Smith")' value={displayName}
                   onChange={(e) => setDisplayName(e.target.value)} />
          )}
          <button className="btn primary" disabled={!email || !fullName}>Add user</button>
        </div>
        {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}
        {tempPassword && (
          <div className="card ai-panel" style={{ marginTop: 8 }}>
            Temporary password (shown once — hand it to them securely): <strong>{tempPassword}</strong>
          </div>
        )}
      </form>
    </>
  );
}

function Offices() {
  const [offices, setOffices] = useState<Office[]>([]);
  const [name, setName] = useState("");
  const load = () => api.get<Office[]>("/offices").then(setOffices);
  useEffect(() => { load(); }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    await api.post("/offices", { name });
    setName("");
    await load();
  };

  return (
    <>
      <h2>Offices</h2>
      <div className="card" style={{ marginBottom: 20 }}>
        {offices.map((o) => (
          <div key={o.id} className="row" style={{ marginBottom: 6 }}>
            <strong>{o.name}</strong><span className="chip">{o.timezone}</span>
          </div>
        ))}
        <form className="row" onSubmit={create}>
          <input placeholder="New office name" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="btn" disabled={!name}>Add office</button>
        </form>
      </div>
    </>
  );
}

function Phones() {
  const [phones, setPhones] = useState<Phone[]>([]);
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [e164, setE164] = useState("");
  const [doctorId, setDoctorId] = useState("");
  const [error, setError] = useState("");

  const load = () => api.get<Phone[]>("/phone-numbers").then(setPhones);
  useEffect(() => {
    load();
    api.get<Doctor[]>("/doctors").then(setDoctors);
  }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await api.post("/phone-numbers", { e164, doctor_id: doctorId || null });
      setE164("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "add failed");
    }
  };

  return (
    <>
      <h2>Texting numbers</h2>
      <div className="card" style={{ marginBottom: 20 }}>
        {phones.map((p) => (
          <div key={p.id} className="row" style={{ marginBottom: 6 }}>
            <strong>{p.e164}</strong>
            {p.mock && <span className="badge yellow">mock — no real texts</span>}
            {p.doctor_id && <span className="chip">
              {doctors.find((d) => d.id === p.doctor_id)?.display_name ?? "doctor"}
            </span>}
            <span className="chip">{p.status}</span>
            {p.status === "active" && (
              <button className="btn" onClick={() => api.del(`/phone-numbers/${p.id}`).then(load)}>
                Retire
              </button>
            )}
          </div>
        ))}
        <form className="row" onSubmit={create}>
          <input placeholder="+15551234567" value={e164} onChange={(e) => setE164(e.target.value)} />
          <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)}>
            <option value="">Practice-wide</option>
            {doctors.map((d) => <option key={d.id} value={d.id}>{d.display_name}</option>)}
          </select>
          <button className="btn" disabled={!e164}>Add number</button>
          {error && <span className="error">{error}</span>}
        </form>
      </div>
    </>
  );
}

function PracticeSettings() {
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.get<{ settings: Record<string, unknown> }>("/practice/settings")
      .then((r) => setSettings(r.settings));
  }, []);

  const toggleStaffSend = async (value: boolean) => {
    const r = await api.patch<{ settings: Record<string, unknown> }>(
      "/practice/settings", { staff_can_send_replies: value },
    );
    setSettings(r.settings);
    setSaved(true);
  };

  return (
    <>
      <h2>Practice policy</h2>
      <div className="card">
        <label className="chip" style={{ cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={settings.staff_can_send_replies === true}
            onChange={(e) => toggleStaffSend(e.target.checked)}
          />{" "}
          Staff and office managers may approve &amp; send replies (default: doctors only)
        </label>
        {saved && <span className="muted" style={{ marginLeft: 10 }}>saved</span>}
      </div>
    </>
  );
}
