import { FormEvent, useEffect, useState } from "react";
import { ApiError, Upload, UploadEntry, api } from "../api";

interface Office { id: string; name: string }
interface Doctor { id: string; display_name: string }

const LOW_CONFIDENCE = 0.7;

export default function Intake() {
  const [offices, setOffices] = useState<Office[]>([]);
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [officeId, setOfficeId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<Upload | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    api.get<Office[]>("/offices").then((o) => {
      setOffices(o);
      if (o.length === 1) setOfficeId(o[0].id);
    });
    api.get<Doctor[]>("/doctors").then(setDoctors);
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file || !officeId) return;
    setError("");
    const form = new FormData();
    form.append("file", file);
    form.append("office_id", officeId);
    try {
      setUpload(await api.postForm<Upload>("/schedule-uploads", form));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "upload failed");
    }
  };

  const patchEntry = async (entryId: string, patch: Record<string, unknown>) => {
    if (!upload) return;
    await api.patch(`/schedule-uploads/${upload.id}/entries/${entryId}`, patch);
    setUpload(await api.get<Upload>(`/schedule-uploads/${upload.id}`));
  };

  const submitForApproval = async () => {
    if (!upload) return;
    setError("");
    try {
      await api.post(`/schedule-uploads/${upload.id}/submit`);
      setMessage("Submitted — the doctor has been notified to review and approve.");
      setUpload(null);
      setFile(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "submit failed");
    }
  };

  if (upload) {
    return (
      <>
        <h1>Review extracted schedule</h1>
        <p className="muted">
          OCR engine: <strong>{upload.ocr_model}</strong>
          {upload.ocr_model === "MOCK" && " (sample data — not read from your file)"} ·
          fields with a yellow outline had low read confidence — verify them against the sheet.
        </p>
        {error && <div className="error" style={{ marginBottom: 10 }}>{error}</div>}
        <div className="card" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Time</th><th>Patient</th><th>Phone</th><th>Procedure</th>
                <th>Doctor</th><th>Included?</th>
              </tr>
            </thead>
            <tbody>
              {upload.entries.map((entry) => (
                <Row key={entry.id} entry={entry} doctors={doctors} patch={patchEntry} />
              ))}
            </tbody>
          </table>
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          <button className="btn primary" onClick={submitForApproval}>
            Submit to doctor for approval
          </button>
          <span className="muted">
            {upload.entries.filter((e) => !e.excluded).length} of {upload.entries.length} patients included ·
            nothing sends until the doctor approves
          </span>
        </div>
      </>
    );
  }

  return (
    <>
      <h1>Upload today's schedule</h1>
      {message && <div className="card" style={{ marginBottom: 12 }}>{message}</div>}
      <form className="card" onSubmit={submit} style={{ maxWidth: 480 }}>
        <div className="row" style={{ flexDirection: "column", alignItems: "stretch" }}>
          <select value={officeId} onChange={(e) => setOfficeId(e.target.value)}>
            <option value="">Choose office…</option>
            {offices.map((o) => (
              <option key={o.id} value={o.id}>{o.name}</option>
            ))}
          </select>
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp,application/pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          {error && <div className="error">{error}</div>}
          <button className="btn primary" disabled={!file || !officeId}>
            Upload &amp; extract patients
          </button>
          <span className="muted">Photo, screenshot, or PDF of the day sheet.</span>
        </div>
      </form>
    </>
  );
}

function Row({
  entry, doctors, patch,
}: {
  entry: UploadEntry;
  doctors: Doctor[];
  patch: (id: string, p: Record<string, unknown>) => Promise<void>;
}) {
  const [phone, setPhone] = useState(entry.phone);
  const lowPhone = (entry.ocr_confidence["phone"] ?? 1) < LOW_CONFIDENCE;

  return (
    <tr className={entry.excluded ? "excluded" : undefined}>
      <td>{entry.time}</td>
      <td>{entry.patient_name}</td>
      <td>
        <input
          className={lowPhone ? "low-confidence" : undefined}
          value={phone}
          title={lowPhone ? "Low OCR confidence — verify against the sheet" : undefined}
          onChange={(e) => setPhone(e.target.value)}
          onBlur={() => phone !== entry.phone && patch(entry.id, { phone_raw: phone })}
        />
      </td>
      <td>{entry.procedure}</td>
      <td>
        <select
          value={entry.resolved_doctor_id ?? ""}
          onChange={(e) => patch(entry.id, { resolved_doctor_id: e.target.value || null })}
        >
          <option value="">— assign —</option>
          {doctors.map((d) => (
            <option key={d.id} value={d.id}>{d.display_name}</option>
          ))}
        </select>
      </td>
      <td className="controls">
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <label className="chip" style={{ cursor: "pointer", whiteSpace: "nowrap" }}>
            <input
              type="checkbox"
              checked={!entry.excluded}
              onChange={(e) => patch(entry.id, {
                excluded: !e.target.checked,
                exclusion_reason: e.target.checked ? null : "excluded by staff",
              })}
            />{" "}
            include
          </label>
          {entry.ocr_crossed_out && <span className="chip">crossed out on sheet</span>}
        </div>
      </td>
    </tr>
  );
}
