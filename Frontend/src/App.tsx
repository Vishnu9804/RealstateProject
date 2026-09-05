import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import ConnectionPage from "./pages/ConnectionPage";
import DashboardPage from "./pages/DashboardPage";
import LandingPagePage from "./pages/LandingPagePage";
import InquiryClientsPage from "./pages/InquiryClientsPage";
import InquiryFormPage from "./pages/InquiryFormPage";
import SettingsPage from "./pages/SettingsPage";
import { ThemeProvider } from "./components/ui/Theme";
import { ToastProvider } from "./components/ui/Toast";
import { StatusProvider } from "./state/StatusProvider";

/**
 * Provider order matters: theme sits outermost because everything below it
 * is painted in its colours, toasts next so any screen can raise one.
 *
 * StatusProvider (polls the internal whatsappDataFetching connection
 * status) wraps ONLY the internal-tool routes under <Layout> — not
 * /whatsapp-inquiry/:token, which is a public page a prospective client
 * opens from a WhatsApp link. That visitor's browser has no business
 * polling an internal ops endpoint, and the page has no ops nav to show a
 * status pill in anyway.
 */
export default function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/whatsapp-inquiry/:token" element={<InquiryFormPage />} />
            <Route
              element={
                <StatusProvider>
                  <Layout />
                </StatusProvider>
              }
            >
              <Route index element={<ConnectionPage />} />
              <Route path="dashboard" element={<DashboardPage />} />
              <Route path="landing-page" element={<LandingPagePage />} />
              <Route path="inquiries" element={<InquiryClientsPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </ThemeProvider>
  );
}
