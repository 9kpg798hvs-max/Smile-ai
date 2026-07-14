import { createContext, useContext, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { Me, api } from "./api";
import Admin from "./pages/Admin";
import Analytics from "./pages/Analytics";
import Approvals from "./pages/Approvals";
import Dashboard from "./pages/Dashboard";
import Inbox from "./pages/Inbox";
import Intake from "./pages/Intake";
import Login from "./pages/Login";
import Logs from "./pages/Logs";
import Notifications from "./pages/Notifications";
import Settings from "./pages/Settings";
import Templates from "./pages/Templates";
import Thread from "./pages/Thread";

const MeContext = createContext<Me | null>(null);
export const useMe = () => useContext(MeContext);

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = () =>
    api.get<Me>("/auth/me").then(setMe).catch(() => setMe(null));

  useEffect(() => {
    refresh().finally(() => setLoading(false));
  }, []);

  if (loading) return null;

  return (
    <MeContext.Provider value={me}>
      <Routes>
        <Route path="/login" element={<Login onLogin={refresh} />} />
        <Route
          path="/*"
          element={me ? <Shell me={me} onLogout={() => setMe(null)} /> : <Navigate to="/login" />}
        />
      </Routes>
    </MeContext.Provider>
  );
}

function Shell({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const navigate = useNavigate();
  const can = (p: string) => me.permissions.includes(p);
  const [mocked, setMocked] = useState<Record<string, unknown>>({});
  useEffect(() => {
    api.get<{ mocked: Record<string, unknown> }>("/health").then((h) => setMocked(h.mocked));
  }, []);

  const logout = async () => {
    await api.post("/auth/logout");
    onLogout();
    navigate("/login");
  };

  const anyMock = mocked.sms === true || mocked.ocr === true;

  return (
    <div className="shell">
      <nav className="sidebar">
        <div className="brand">
          SmileFlow AI
          <small>patient follow-up</small>
        </div>
        <NavLink to="/" end className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
          Dashboard
        </NavLink>
        <NavLink to="/inbox" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
          Inbox
        </NavLink>
        {can("approve_patient_list") && (
          <NavLink to="/approvals" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            Approvals
          </NavLink>
        )}
        {can("upload_schedule") && (
          <NavLink to="/intake" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            Upload schedule
          </NavLink>
        )}
        <NavLink to="/notifications" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
          Notifications
        </NavLink>
        {can("manage_templates") && (
          <NavLink to="/templates" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            Templates
          </NavLink>
        )}
        {can("configure_send_times") && (
          <NavLink to="/settings" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            Settings
          </NavLink>
        )}
        {can("view_analytics") && (
          <NavLink to="/analytics" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            Analytics
          </NavLink>
        )}
        {can("view_delivery_logs") && (
          <NavLink to="/logs" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            Logs
          </NavLink>
        )}
        {(can("manage_users") || can("manage_offices") || can("manage_phone_numbers")) && (
          <NavLink to="/admin" className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            Admin
          </NavLink>
        )}
        <div className="spacer" />
        {anyMock && <div className="mock-banner">MOCK MODE — no real texts are sent</div>}
        <div className="whoami">
          {me.full_name}
          <br />
          {me.role.replace("_", " ")} · <a onClick={logout} style={{ cursor: "pointer" }}>sign out</a>
        </div>
      </nav>
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/inbox" element={<Inbox />} />
          <Route path="/inbox/:conversationId" element={<Thread />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/intake" element={<Intake />} />
          <Route path="/notifications" element={<Notifications />} />
          <Route path="/templates" element={<Templates />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}
