import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import RequireAuth from "./components/RequireAuth";
import AgentsPage from "./pages/AgentsPage";
import BrokerRequirementsPage from "./pages/BrokerRequirementsPage";
import BuilderProjectsPage from "./pages/BuilderProjectsPage";
import ClientMatchesPage from "./pages/ClientMatchesPage";
import ConnectionPage from "./pages/ConnectionPage";
import DashboardPage from "./pages/DashboardPage";
import LandingPagePage from "./pages/LandingPagePage";
import InquiryClientsPage from "./pages/InquiryClientsPage";
import LoginPage from "./pages/LoginPage";
import SelectPropertyPage from "./pages/SelectPropertyPage";
import SettingsPage from "./pages/SettingsPage";
import UserManagementPage from "./pages/UserManagementPage";
import VisitsPage from "./pages/VisitsPage";
import { AuthProvider } from "./state/AuthProvider";
import { OwnerVerificationProvider } from "./state/OwnerVerificationProvider";
import { ThemeProvider } from "./components/ui/Theme";
import { ToastProvider } from "./components/ui/Toast";
import { StatusProvider } from "./state/StatusProvider";

/**
 * Provider order matters: theme sits outermost because everything below it
 * is painted in its colours, toasts next so any screen can raise one,
 * AuthProvider next so both the login screen and every protected route can
 * read the session.
 *
 * /login is the one route outside RequireAuth — everything else now
 * requires a valid session, mirroring Backend/main.py's router-level
 * `dependencies=[Depends(get_current_user)]`. StatusProvider (polls the
 * internal whatsappDataFetching connection status) still wraps every
 * protected route, because every one of them is still an internal-tool page.
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
        <AuthProvider>
          <OwnerVerificationProvider>
            <BrowserRouter>
              <Routes>
                <Route path="login" element={<LoginPage />} />
                <Route element={<RequireAuth />}>
                  <Route
                    element={
                      <StatusProvider>
                        <Layout />
                      </StatusProvider>
                    }
                  >
                    <Route index element={<ConnectionPage />} />
                    {/* The Properties page. Its component is still called
                        DashboardPage (it is the page this tool was built
                        around), but the address says what the page IS. */}
                    <Route path="properties" element={<DashboardPage />} />
                    {/* The address it used to live at. Kept as a redirect so
                        a bookmark, an open tab or a pasted link from before
                        the rename still lands on the page rather than on a
                        blank screen. */}
                    <Route path="dashboard" element={<Navigate to="/properties" replace />} />
                    <Route path="requirements" element={<BrokerRequirementsPage />} />
                    <Route path="builder-projects" element={<BuilderProjectsPage />} />
                    <Route path="landing-page" element={<LandingPagePage />} />
                    <Route path="inquiries" element={<InquiryClientsPage />} />
                    <Route
                      path="inquiries/:phone/matches"
                      element={<ClientMatchesPage />}
                    />
                    <Route path="select-property" element={<SelectPropertyPage />} />
                    <Route path="agents" element={<AgentsPage />} />
                    <Route path="visits" element={<VisitsPage />} />
                    <Route path="team" element={<UserManagementPage />} />
                    <Route path="settings" element={<SettingsPage />} />
                  </Route>
                </Route>
              </Routes>
            </BrowserRouter>
          </OwnerVerificationProvider>
        </AuthProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}
