import { useCallback, useEffect, useState } from "react";
import { authApi } from "../api/authApi";
import type { UserSummary } from "../api/types";
import { plainError } from "../lib/apiError";
import { relativeTime } from "../lib/formatters";
import ChangePasswordDialog from "../components/ChangePasswordDialog";
import EmployeeFormDialog from "../components/EmployeeFormDialog";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import { useToast } from "../components/ui/Toast";
import { Avatar, Button, EmptyState, Note, Panel, SkeletonRows } from "../components/ui/Primitives";
import { IconEdit, IconLock, IconPlus, IconTrash, IconUserCheck, IconUsers } from "../components/ui/Icons";
import { useAuth } from "../state/AuthProvider";

export default function UserManagementPage() {
  const auth = useAuth();
  const toast = useToast();
  const [employees, setEmployees] = useState<UserSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<UserSummary | null>(null);
  const [removing, setRemoving] = useState<UserSummary | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [changingPassword, setChangingPassword] = useState(false);

  const load = useCallback(async () => {
    try {
      const list = await authApi.listEmployees();
      setEmployees(list);
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
          <h1 className="page-title">Team &amp; access</h1>
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

      <Note tone="ok">Adding or changing a login asks you to confirm your own admin password first.</Note>

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
