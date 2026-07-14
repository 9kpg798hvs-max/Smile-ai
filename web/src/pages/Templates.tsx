import { FormEvent, useEffect, useState } from "react";
import { ApiError, api } from "../api";

interface Template {
  id: string;
  name: string;
  body: string;
  tone: string;
  doctor_id: string | null;
  procedure: string | null;
  is_default: boolean;
}
interface Doctor { id: string; display_name: string }

const PLACEHOLDER_HELP =
  "Placeholders: {first_name} {doctor_name} {office_name} {procedure} {treatment_date} {office_phone}";

export default function Templates() {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [name, setName] = useState("");
  const [body, setBody] = useState("Hi {first_name}, this is {doctor_name} checking on you. How are you feeling?");
  const [doctorId, setDoctorId] = useState("");
  const [procedure, setProcedure] = useState("");
  const [error, setError] = useState("");

  const load = () => api.get<Template[]>("/templates").then(setTemplates);
  useEffect(() => {
    load().catch((e) => setError(String(e.message ?? e)));
    api.get<Doctor[]>("/doctors").then(setDoctors);
  }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await api.post("/templates", {
        name, body,
        doctor_id: doctorId || null,
        procedure: procedure || null,
      });
      setName("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "create failed");
    }
  };

  return (
    <>
      <h1>Message templates</h1>
      {error && <div className="error">{error}</div>}

      {templates.map((t) => (
        <TemplateCard key={t.id} t={t} doctors={doctors} reload={load} />
      ))}

      <h2>New template</h2>
      <form className="card" onSubmit={create}>
        <div className="row" style={{ flexDirection: "column", alignItems: "stretch" }}>
          <input placeholder="Template name" value={name} onChange={(e) => setName(e.target.value)} />
          <textarea rows={3} value={body} onChange={(e) => setBody(e.target.value)} />
          <span className="muted">{PLACEHOLDER_HELP}</span>
          <div className="row">
            <select value={doctorId} onChange={(e) => setDoctorId(e.target.value)}>
              <option value="">All doctors</option>
              {doctors.map((d) => <option key={d.id} value={d.id}>{d.display_name}</option>)}
            </select>
            <input placeholder="Procedure (optional)" value={procedure}
                   onChange={(e) => setProcedure(e.target.value)} />
            <button className="btn primary" disabled={!name || !body}>Create</button>
          </div>
        </div>
      </form>
    </>
  );
}

function TemplateCard({ t, doctors, reload }: { t: Template; doctors: Doctor[]; reload: () => Promise<unknown> }) {
  const [body, setBody] = useState(t.body);
  const [preview, setPreview] = useState<{ rendered: string; unresolved_placeholders: string[] } | null>(null);
  const [error, setError] = useState("");
  const doctorName = doctors.find((d) => d.id === t.doctor_id)?.display_name;

  const save = async () => {
    setError("");
    try {
      await api.patch(`/templates/${t.id}`, { body });
      setPreview(null);
      await reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "save failed");
    }
  };

  const doPreview = async () => {
    setPreview(await api.post(`/templates/${t.id}/preview`));
  };

  const remove = async () => {
    if (!window.confirm(`Delete template "${t.name}"?`)) return;
    try {
      await api.del(`/templates/${t.id}`);
      await reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "delete failed");
    }
  };

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ marginBottom: 8 }}>
        <strong>{t.name}</strong>
        {t.is_default && <span className="chip">practice default</span>}
        {doctorName && <span className="chip">{doctorName}</span>}
        {t.procedure && <span className="chip">{t.procedure}</span>}
      </div>
      <textarea rows={2} value={body} onChange={(e) => setBody(e.target.value)} />
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn" onClick={doPreview}>Preview</button>
        <button className="btn primary" disabled={body === t.body} onClick={save}>Save</button>
        <button className="btn danger" onClick={remove}>Delete</button>
        {error && <span className="error">{error}</span>}
      </div>
      {preview && (
        <div className="card ai-panel" style={{ marginTop: 10 }}>
          <div className="muted">Preview with sample data:</div>
          <div>{preview.rendered}</div>
          {preview.unresolved_placeholders.length > 0 && (
            <div className="error" style={{ marginTop: 6 }}>
              Unfilled placeholders: {preview.unresolved_placeholders.join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
