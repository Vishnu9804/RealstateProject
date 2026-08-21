import { NavLink, Outlet } from "react-router-dom";

const linkStyle = ({ isActive }: { isActive: boolean }): React.CSSProperties => ({
  padding: "8px 16px",
  textDecoration: "none",
  fontWeight: isActive ? 600 : 400,
  color: isActive ? "#1a73e8" : "#333",
  borderBottom: isActive ? "2px solid #1a73e8" : "2px solid transparent",
});

export default function Layout() {
  return (
    <div>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "0 16px",
          background: "#fff",
          borderBottom: "1px solid #e0e0e0",
        }}
      >
        <strong style={{ marginRight: 24 }}>Real Estate WhatsApp Ingestion</strong>
        <nav style={{ display: "flex" }}>
          <NavLink to="/" end style={linkStyle}>
            Connection
          </NavLink>
          <NavLink to="/dashboard" style={linkStyle}>
            Properties
          </NavLink>
        </nav>
      </header>
      <main style={{ padding: 24 }}>
        <Outlet />
      </main>
    </div>
  );
}
