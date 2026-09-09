import { useNavigate, useParams } from "react-router-dom";
import ClientMatchesDialog from "../components/ClientMatchesDialog";

/**
 * The old "View Matches" route, kept alive for anything already pointing at
 * it (bookmarks, the browser back button, a link pasted into a chat).
 *
 * Matches are no longer a screen of their own: the Inquiries table's
 * "N properties" cell opens components/ClientMatchesDialog.tsx over the
 * client list instead, so the operator never loses their place in the
 * table they were reading. That dialog is the entire feature, so this
 * route simply mounts it and sends you back to Inquiries when it closes —
 * one implementation, reachable two ways, with no second copy to keep in
 * step.
 */
export default function ClientMatchesPage() {
  const { phone = "" } = useParams<{ phone: string }>();
  const navigate = useNavigate();

  return <ClientMatchesDialog phone={phone} onClose={() => navigate("/inquiries")} />;
}
