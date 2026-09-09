import { BrowserRouter, Route, Routes, useLocation } from "react-router-dom";
import { isHomeRoute } from "./hooks/useScroll";
import SiteFooter from "./components/SiteFooter";
import SiteHeader from "./components/SiteHeader";
import HomePage from "./pages/HomePage";
import PropertyPage from "./pages/PropertyPage";

/**
 * Three routes, but only two pages: the scrolling home page, one property,
 * and /enquire/:token — which IS the home page, entered at the requirements
 * form at the bottom of it.
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
  const onHome = isHomeRoute(location.pathname);

  return (
    <>
      <SiteHeader />
      <Routes>
        <Route path="/" element={<HomePage />} />
        {/* The link our WhatsApp and Instagram messages send (built from
            Backend/Config/settings.py's inquiry_form_base_url). Same page as
            "/" — HomePage reads the token, prefills the form at the bottom
            with what we already have on file, and scrolls the visitor down
            to it. A form link should land on the business's own site, not a
            bare form on a different app. */}
        <Route path="/enquire/:token" element={<HomePage />} />
        <Route path="/property/:recordId" element={<PropertyPage />} />
        {/* Any other URL is a mistyped or stale link; the home page is a
            better answer than a dead end. */}
        <Route path="*" element={<HomePage />} />
      </Routes>
      <SiteFooter onHome={onHome} />
    </>
  );
}
