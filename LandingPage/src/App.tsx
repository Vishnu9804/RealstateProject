import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import SiteFooter from "./components/SiteFooter";
import SiteHeader from "./components/SiteHeader";
import HomePage from "./pages/HomePage";
import PropertyPage from "./pages/PropertyPage";

/**
 * Two routes: the scrolling home page, and one property.
 *
 * There is no provider stack here, unlike the internal tool
 * (Frontend/src/App.tsx) — no theme, no toasts, no status polling. A public
 * marketing site is dark-only by design, has nothing to poll, and every
 * message it needs to show belongs inline next to whatever the visitor was
 * doing rather than in a corner.
 *
 * The gold cursor itself is plain CSS (styles/cursor.css), not a component
 * — see that file's own comment for why.
 */
export default function App() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  );
}

/** Inside the router, so the header and footer can know which route is
 *  showing — their section links behave differently on the home page than
 *  on a property page. */
function Shell() {
  const location = useLocation();
  const onHome = location.pathname === "/";

  return (
    <>
      <SiteHeader />
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/property/:recordId" element={<PropertyPage />} />
        {/* Any other URL is a mistyped or stale link; the home page is a
            better answer than a dead end. */}
        <Route path="*" element={<HomePage />} />
      </Routes>
      <SiteFooter onHome={onHome} />
    </>
  );
}
