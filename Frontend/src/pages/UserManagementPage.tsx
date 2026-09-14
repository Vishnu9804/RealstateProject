import { useCallback, useEffect, useState } from "react";
import { authApi } from "../api/authApi";
import type { OwnerVerificationStatus, UserSummary } from "../api/types";
import { plainError } from "../lib/apiError";
import { relativeTime } from "../lib/formatters";
import ChangePasswordDialog from "../components/ChangePasswordDialog";
import EmployeeFormDialog from "../components/EmployeeFormDialog";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import { useToast } from "../components/ui/Toast";
import { Avatar, Button, EmptyState, Note, Panel, SkeletonRows } from "../components/ui/Primitives";
import { IconEdit, IconLock, IconPlus, IconTrash, IconUserCheck, IconUsers } from "../components/ui/Icons";
import { useAuth } from "../state/AuthProvider";

function VerificationNote({ status }: { status: OwnerVerificationStatus | null }) {
  if (!status) return null;
  if (status.method === "password") {
    return (
      <Note tone="warn">
        <strong>Owner phone verification is off.</strong> Adding or changing a login asks for your password instead of a
        WhatsApp code, and "Forgot password" is unavailable. Set <code>ADMIN_PHONE</code> in <code>Backend/.env</code> and
        restart the backend to turn it on.
      </Note>
    );
  }
  if (!status.available) {
    return (
      <Note tone="bad">
        <strong>Verification codes can't be sent right now.</strong>{" "}
        {status.reason === "invalid_owner_phone"
          ? "ADMIN_PHONE in Backend/.env isn't a valid number."
          : "No WhatsApp number is connected — link one on the Connection page."}{" "}
        Adding or changing logins waits until then; removing access still works.
      </Note>
    );
  }
  return (
    <Note tone="ok">
      Adding or changing a login is confirmed with a code sent to the owner's WhatsApp ending in{" "}
      <strong>{status.phone_hint?.slice(-2)}</strong>.
    </Note>
  );
}

export default function UserManagementPage() {
  const auth = useAuth();
  const toast = useToast();
  const [employees, setEmployees] = useState<UserSummary[] | null>(null);
  const [verification, setVerification] = useState<OwnerVerificationStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<UserSummary | null>(null);
  const [removing, setRemoving] = useState<UserSummary | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [changingPassword, setChangingPassword] = useState(false);

  const load = useCallback(async () => {
    try {
      const [list, status] = await Promise.all([authApi.listEmployees(), authApi.getOwnerVerification()]);
      setEmployees(list);
      setVerification(status);
      setError(null);
    } catch (err) {
      setError(plainError(err));
    }
  }, []);

  useEffect(() => {
    if (auth.isAdmin) void load();
  }, [auth.isAdmin, load]);

  async function handleRemove() {
    if (!removing) return;
    setRemoveBusy(true);
    try {
      await authApi.deleteEmployee(removing.user_id);
      setEmployees((prev) => (prev ? prev.filter((e) => e.user_id !== removing.user_id) : prev));
      toast.push({ tone: "ok", title: "Access removed", message: `${removing.username} was signed out.` });
      setRemoving(null);
    } catch (err) {
      toast.push({ tone: "bad", title: "Couldn't remove access", message: plainError(err) });
    } finally {
      setRemoveBusy(false);
    }
  }

  if (!auth.isAdmin) {
    return <EmptyState icon={<IconUserCheck size={22} />} title="Owner only" body="Only the owner account can manage team logins." />;
  }

  return (
    <div className="stack stack-5">
      <header className="section-head">
        <div>
          <div className="section-head__eyebrow">Access control</div>
          <h1 className="page-title">Team &amp; access</h1>
          <p className="section-head__sub">
            Employees can use every page, but can't delete records, mark properties sold out, unlink WhatsApp, disconnect
            Instagram or manage logins — and can't change their own username or password.
          </p>
        </div>
        <div className="row-flex" style={{ gap: 8 }}>
          <Button variant="ghost" icon={<IconLock size={15} />} onClick={() => setChangingPassword(true)}>
            Change my password
          </Button>
          <Button variant="primary" icon={<IconPlus size={15} />} onClick={() => setShowAdd(true)}>
            Add employee
          </Button>
        </div>
      </header>

      <VerificationNote status={verification} />

      {error && employees === null ? (
        <Note tone="bad">{error}</Note>
      ) : employees === null ? (
        <Panel>
          <SkeletonRows rows={3} />
        </Panel>
      ) : employees.length === 0 ? (
        <EmptyState
          icon={<IconUsers size={22} />}
          title="No employees yet"
          body="Add a login for each staff member and hand them the username and password directly."
        />
      ) : (
        <div className="table-frame anim-rise">
          <div className="table-scroll">
            <table className="table">
              <thead>
                <tr>
                  <th>Employee</th>
                  <th>Added</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {employees.map((employee) => (
                  <tr key={employee.user_id}>
                    <td>
                      <span className="row-flex" style={{ gap: 10, flexWrap: "nowrap" }}>
                        <Avatar name={employee.username} size={28} />
                        {employee.username}
                      </span>
                    </td>
                    <td>{employee.created_at ? relativeTime(new Date(employee.created_at)) : "—"}</td>
                    <td>
                      <div className="row-flex" style={{ gap: 6, flexWrap: "nowrap" }}>
                        <Button size="sm" variant="ghost" icon={<IconEdit size={14} />} onClick={() => setEditing(employee)}>
                          Edit
                        </Button>
                        <Button size="sm" className="btn--danger" icon={<IconTrash size={14} />} onClick={() => setRemoving(employee)}>
                          Remove access
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {showAdd && (
        <EmployeeFormDialog
          onClose={() => setShowAdd(false)}
          onSaved={(created) => {
            setEmployees((prev) => [...(prev ?? []), created]);
            setShowAdd(false);
          }}
        />
      )}

      {editing && (
        <EmployeeFormDialog
          employee={editing}
          onClose={() => setEditing(null)}
          onSaved={(updated) => {
            setEmployees((prev) => (prev ? prev.map((e) => (e.user_id === updated.user_id ? updated : e)) : prev));
            setEditing(null);
          }}
        />
      )}

      {removing && (
        <ConfirmDialog
          title="Remove this employee's access?"
          body={
            <p>
              <strong>{removing.username}</strong> is signed out immediately and can't sign in again.
            </p>
          }
          confirmLabel="Remove access"
          tone="danger"
          busy={removeBusy}
          onConfirm={handleRemove}
          onClose={() => setRemoving(null)}
        />
      )}

      {changingPassword && <ChangePasswordDialog onClose={() => setChangingPassword(false)} />}
    </div>
  );
}
