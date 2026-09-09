import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import AgentsPage from "./pages/AgentsPage";
import ClientMatchesPage from "./pages/ClientMatchesPage";
import ConnectionPage from "./pages/ConnectionPage";
import DashboardPage from "./pages/DashboardPage";
import LandingPagePage from "./pages/LandingPagePage";
import InquiryClientsPage from "./pages/InquiryClientsPage";
import SelectPropertyPage from "./pages/SelectPropertyPage";
import SettingsPage from "./pages/SettingsPage";
import { ThemeProvider } from "./components/ui/Theme";
import { ToastProvider } from "./components/ui/Toast";
import { StatusProvider } from "./state/StatusProvider";

/**
 * Provider order matters: theme sits outermost because everything below it
 * is painted in its colours, toasts next so any screen can raise one.
 *
 * StatusProvider (polls the internal whatsappDataFetching connection
 * status) wraps every route here, because every route here is now an
 * internal-tool one.
 *
 * There used to be one exception: /whatsapp-inquiry/:token, the public
 * requirements form a prospective client opened from a WhatsApp link. That
 * form now lives on the public site instead — LandingPage/'s /enquire/:token
 * renders it inside the landing page itself (Backend/Config/settings.py's
 * inquiry_form_base_url points there), so a client following our link lands
 * on the business's own website rather than a bare form on the ops app.
 */
export default function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
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
              <Route path="inquiries/:phone/matches" element={<ClientMatchesPage />} />
              <Route path="select-property" element={<SelectPropertyPage />} />
              <Route path="agents" element={<AgentsPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </ThemeProvider>
  );
}
