import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import ConnectionPage from "./pages/ConnectionPage";
import DashboardPage from "./pages/DashboardPage";
import SettingsPage from "./pages/SettingsPage";
import { ThemeProvider } from "./components/ui/Theme";
import { ToastProvider } from "./components/ui/Toast";
import { StatusProvider } from "./state/StatusProvider";

/**
 * Provider order matters: theme sits outermost because everything below it
 * is painted in its colours, toasts next so any screen (and the status
 * poller) can raise one, and the status poll innermost — it is the only
 * provider that talks to the network.
 */
export default function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <BrowserRouter>
          <StatusProvider>
            <Routes>
              <Route element={<Layout />}>
                <Route index element={<ConnectionPage />} />
                <Route path="dashboard" element={<DashboardPage />} />
                <Route path="settings" element={<SettingsPage />} />
              </Route>
            </Routes>
          </StatusProvider>
        </BrowserRouter>
      </ToastProvider>
    </ThemeProvider>
  );
}
