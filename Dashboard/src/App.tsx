import { useState } from "react";
import { ToastProvider } from "./components/Toast";
import { IconActivity, IconCpu, IconDatabase, IconMessage, IconServer, IconZap } from "./components/Icons";
import SuratAreaKnowledgeBasePage from "./pages/SuratAreaKnowledgeBasePage";
import LLMCostPage from "./pages/LLMCostPage";
import NeonDbPage from "./pages/NeonDbPage";
import BackendPage from "./pages/BackendPage";
import MessageToModelPage from "./pages/MessageToModelPage";

interface Tab {
  id: string;
  label: string;
  icon: (props: { size?: number }) => JSX.Element;
  render: () => JSX.Element;
}

const TABS: Tab[] = [
  {
    id: "surat-area-knowledge-base",
    label: "Surat Area Knowledge Base",
    icon: IconDatabase,
    render: () => <SuratAreaKnowledgeBasePage />,
  },
  {
    id: "llm-cost",
    label: "LLM Cost",
    icon: IconCpu,
    render: () => <LLMCostPage />,
  },
  {
    id: "neon-db",
    label: "Neon DB",
    icon: IconServer,
    render: () => <NeonDbPage />,
  },
  {
    id: "backend",
    label: "Backend",
    icon: IconActivity,
    render: () => <BackendPage />,
  },
  {
    id: "message-to-model",
    label: "Message to Model",
    icon: IconMessage,
    render: () => <MessageToModelPage />,
  },
];

export default function App() {
  const [activeTab, setActiveTab] = useState(TABS[0].id);
  const current = TABS.find((tab) => tab.id === activeTab) ?? TABS[0];

  return (
    <ToastProvider>
      <div className="shell">
        <header className="topbar">
          <div className="topbar__inner">
            <div className="brand">
              <span className="brand__mark">
                <IconZap size={19} />
              </span>
              <span className="brand__text">
                <span className="brand__name">Dashboard</span>
                <span className="brand__sub">Manibhadra Real Estate — internal</span>
              </span>
            </div>

            <nav className="dock" aria-label="Primary">
              {TABS.map((tab) => {
                const Icon = tab.icon;
                const active = tab.id === activeTab;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    className={`dock__link${active ? " dock__link--active" : ""}`}
                    aria-current={active ? "page" : undefined}
                    onClick={() => setActiveTab(tab.id)}
                  >
                    <Icon size={16} />
                    <span>{tab.label}</span>
                  </button>
                );
              })}
            </nav>
          </div>
        </header>

        <main className="shell__main">
          <div className="page" key={current.id}>
            {current.render()}
          </div>
        </main>
      </div>
    </ToastProvider>
  );
}
