import { useEffect, useState } from 'react';
import { DEV_AUTH, LOGIN_URL, currentUser, post, setSession } from './api.js';
import { ROLES, hasRole, sessionUser } from './lib.js';
import Queue from './Queue.jsx';
import Records from './Records.jsx';
import Team from './Team.jsx';

export default function App() {
  const [user, setUser] = useState(currentUser);
  const [tab, setTab] = useState('queue');

  useEffect(() => {
    const sync = () => setUser(currentUser());
    window.addEventListener('re-auth', sync);
    return () => window.removeEventListener('re-auth', sync);
  }, []);

  useEffect(() => { // Cognito tokens last 1 h with no refresh: sign out when it runs out.
    if (!user?.exp) return;
    const t = setTimeout(() => setSession(null), Math.max(0, user.exp * 1000 - Date.now()));
    return () => clearTimeout(t);
  }, [user]);

  if (!user) return <Login />;
  if (!hasRole(user.groups, 'specialist')) return <Pending user={user} />;
  const tabs = [['queue', 'Call queue'], ['records', 'Records'], ...(hasRole(user.groups, 'admin') ? [['team', 'Team']] : [])];

  return (
    <div className="page">
      <header className="topbar">
        <div>
          <p className="eyebrow">One Degree · Resource Engine</p>
          <nav className="tabs" role="tablist">
            {tabs.map(([k, label]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={'tab' + (tab === k ? ' on' : '')} onClick={() => setTab(k)}>{label}</button>
            ))}
          </nav>
        </div>
        <div className="who">
          <span><b>{user.name || user.email}</b><span className="small">{user.groups.join(', ') || 'no role'}</span></span>
          <button className="btn sm" onClick={() => setSession(null)}>Sign out</button>
        </div>
      </header>
      {tab === 'queue' ? <Queue user={user} /> : tab === 'records' ? <Records user={user} /> : <Team user={user} />}
    </div>
  );
}

/** Signed in, but no admin has given this account a role yet (the server refuses it with 403). */
function Pending({ user }) {
  return (
    <div className="login">
      <div className="card login-card">
        <p className="eyebrow">One Degree · Resource Engine</p>
        <h1>Waiting for approval</h1>
        <p className="sub">You’re signed in as <b>{user.email}</b>, but an admin hasn’t given your account a role yet. Sign in again once you hear back.</p>
        <button className="btn" onClick={() => setSession(null)}>Sign out</button>
      </div>
    </div>
  );
}

function SignUp({ onBack }) {
  const [f, setF] = useState({ email: '', name: '', organization: '', reason: '' });
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [err, setErr] = useState('');
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr('');
    try { await post('/auth/signup', f); setDone(true); } catch (x) { setErr(x.status === 429 ? 'Too many requests are waiting right now. Try again later.' : x.message); }
    setBusy(false);
  };

  if (done) return (
    <div className="stack">
      <div className="done">Request sent. An admin will review it. Once approved you’ll get an email from Cognito with a temporary password; sign in with it and choose your own.</div>
      <button className="btn" onClick={onBack}>Back to sign-in</button>
    </div>
  );
  return (
    <form onSubmit={submit} className="stack">
      <label className="field">Work email<input type="email" required maxLength={254} value={f.email} onChange={set('email')} autoFocus /></label>
      <label className="field">Full name<input required maxLength={100} value={f.name} onChange={set('name')} /></label>
      <label className="field">Organization<input maxLength={200} value={f.organization} onChange={set('organization')} placeholder="211 call center" /></label>
      <label className="field">Why do you need access?<textarea rows={3} maxLength={1000} value={f.reason} onChange={set('reason')} placeholder="I verify food bank listings for Solano County" /></label>
      {err && <div className="error">{err}</div>}
      <button className="btn primary" type="submit" disabled={busy}>{busy ? 'Sending…' : 'Request access'}</button>
      <button className="btn" type="button" onClick={onBack}>Back to sign-in</button>
    </form>
  );
}

function Login() {
  const [signup, setSignup] = useState(false);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('specialist');
  const [token, setToken] = useState('');
  const [err, setErr] = useState('');

  const devSignIn = (e) => {
    e.preventDefault();
    setSession({ devUser: email.trim(), devGroups: [role] });
  };
  const useToken = () => {
    if (!sessionUser({ token: token.trim() })) return setErr('That token is invalid or expired.');
    setSession({ token: token.trim() });
  };

  return (
    <div className="login">
      <div className="card login-card">
        <p className="eyebrow">One Degree · Resource Engine</p>
        <h1>{signup ? 'Request an account' : 'Specialist sign-in'}</h1>
        <p className="sub">{signup ? 'An admin approves every new specialist account.' : 'Verify the facilities the AI and the data feeds couldn’t confirm: call them, check the evidence, and fix the record.'}</p>

        {signup ? <SignUp onBack={() => setSignup(false)} /> : DEV_AUTH ? (
          <form onSubmit={devSignIn} className="stack">
            <p className="hint">Local backend (<code>AUTH_MODE=dev</code>): no password, your email and role are sent as dev headers.</p>
            <label className="field">Email
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="ana@example.org" autoFocus />
            </label>
            <label className="field">Role
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                {ROLES.map((r) => <option key={r}>{r}</option>)}
              </select>
            </label>
            <button className="btn primary" type="submit">Sign in</button>
          </form>
        ) : (
          <div className="stack">
            <a className="btn primary" href={LOGIN_URL}>Sign in with your account</a>
            <p className="hint">After sign-in the server sends you to its <code>UI_URL</code>. Until that points at this app you land on the server’s <code>/ui/</code> page: copy <code>localStorage.re_token</code> there and paste it below.</p>
          </div>
        )}

        {!signup && <p className="hint">No account? <button className="link" onClick={() => setSignup(true)}>Request access</button></p>}

        {!signup && <details className="advanced">
          <summary>Paste an id_token instead</summary>
          <div className="stack">
            <textarea rows={3} value={token} onChange={(e) => { setToken(e.target.value); setErr(''); }} placeholder="eyJraWQi…" />
            {err && <div className="error">{err}</div>}
            <button className="btn sm" onClick={useToken} disabled={!token.trim()}>Use token</button>
          </div>
        </details>}
      </div>
    </div>
  );
}
