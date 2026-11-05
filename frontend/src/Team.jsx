import { useCallback, useEffect, useState } from 'react';
import { api, enc, post } from './api.js';
import { ROLES, when } from './lib.js';
import { ErrorBox } from './ui.jsx';

const STATUS_LABEL = { FORCE_CHANGE_PASSWORD: 'Invited, not signed in yet', CONFIRMED: 'Active' };

// Admin only: approve sign-up requests and manage who has which role.
export default function Team({ user }) {
  const [show, setShow] = useState('pending');
  const [reqs, setReqs] = useState(null);
  const [users, setUsers] = useState(null);
  const [roleFor, setRoleFor] = useState({}); // email -> role picked for approval
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');

  const load = useCallback(() => {
    setErr('');
    api('/admin/signups' + (show ? '?status=' + show : '')).then(setReqs).catch((e) => { setErr(e.message); setReqs([]); });
    api('/admin/users').then(setUsers).catch((e) => { setErr(e.message); setUsers([]); });
  }, [show]);
  useEffect(() => { load(); }, [load]);

  const act = async (key, fn, done) => {
    setBusy(key); setErr(''); setMsg('');
    try { await fn(); setMsg(done); load(); } catch (e) { setErr(e.message); }
    setBusy('');
  };

  const approve = (r) => {
    const role = roleFor[r.email] || 'specialist';
    act(r.email, () => post(`/admin/signups/${enc(r.email)}/approve`, { role }),
      `Approved ${r.email} as ${role}. Cognito is emailing them a temporary password.`);
  };
  const reject = (r) => {
    const reason = window.prompt(`Reject ${r.email}? Optional reason (kept for the record):`, '');
    if (reason === null) return;
    act(r.email, () => post(`/admin/signups/${enc(r.email)}/reject`, { reason }), `Rejected ${r.email}.`);
  };
  const setRole = (u, role) => {
    if (!role && !window.confirm(`Remove every role from ${u.email}? They lose access (a token they already have works for up to 1 hour).`)) return;
    act(u.email, () => post(`/admin/users/${enc(u.email)}/role`, { role: role || null }),
      role ? `${u.email} is now ${role}. They see it after their next sign-in.` : `Access removed for ${u.email}.`);
  };

  return (
    <div className="stack">
      <ErrorBox>{err}</ErrorBox>
      {msg && <div className="done">{msg}</div>}

      <section className="card">
        <div className="section-head">
          <h2>Access requests</h2>
          <select aria-label="Show" value={show} onChange={(e) => setShow(e.target.value)}>
            <option value="pending">Pending</option><option value="approved">Approved</option><option value="rejected">Rejected</option><option value="">All</option>
          </select>
        </div>
        {!reqs ? <p className="muted pad">Loading…</p> : !reqs.length ? (
          <div className="empty"><b>No {show} requests</b><p className="muted">New sign-ups from the login page show up here.</p></div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="table">
              <thead><tr><th>Person</th><th>Why</th><th>Requested</th><th>{show === 'pending' ? 'Decision' : 'Outcome'}</th></tr></thead>
              <tbody>
                {reqs.map((r) => (
                  <tr key={r.email}>
                    <td><b>{r.name}</b><span className="small">{r.email}</span><span className="small">{r.organization}</span></td>
                    <td style={{ maxWidth: 320 }}>{r.reason || <span className="muted">—</span>}</td>
                    <td className="small">{when(r.requested_at)}</td>
                    <td>
                      {r.status === 'pending' ? (
                        <div className="inline">
                          <select aria-label={`Role for ${r.email}`} value={roleFor[r.email] || 'specialist'} onChange={(e) => setRoleFor({ ...roleFor, [r.email]: e.target.value })}>
                            {ROLES.map((x) => <option key={x}>{x}</option>)}
                          </select>
                          <button className="btn sm primary" disabled={busy === r.email} onClick={() => approve(r)}>Approve</button>
                          <button className="btn sm" disabled={busy === r.email} onClick={() => reject(r)}>Reject</button>
                        </div>
                      ) : (
                        <span className="small">{r.status}{r.role ? ` as ${r.role}` : ''} by {r.decided_by}<br />{when(r.decided_at)}{r.decision_reason ? ` · ${r.decision_reason}` : ''}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <div className="section-head"><h2>Specialists and staff</h2><button className="btn sm" onClick={load}>Reload</button></div>
        {!users ? <p className="muted pad">Loading…</p> : !users.length ? <p className="muted pad">No accounts yet.</p> : (
          <div style={{ overflowX: 'auto' }}>
            <table className="table">
              <thead><tr><th>Person</th><th>Status</th><th>Role</th></tr></thead>
              <tbody>
                {users.map((u) => {
                  const role = ROLES.slice().reverse().find((r) => u.groups.includes(r)) || '';
                  const self = u.email === user.email;
                  return (
                    <tr key={u.email}>
                      <td><b>{u.name || u.email}</b>{u.name && <span className="small">{u.email}</span>}</td>
                      <td className="small">{u.enabled === false ? 'Disabled' : STATUS_LABEL[u.status] || u.status}<br />since {when(u.created_at)}</td>
                      <td>
                        <select aria-label={`Role of ${u.email}`} value={role} disabled={busy === u.email || self} title={self ? 'You can’t change your own role' : ''}
                          onChange={(e) => setRole(u, e.target.value)}>
                          {ROLES.map((x) => <option key={x}>{x}</option>)}
                          <option value="">no access</option>
                        </select>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
