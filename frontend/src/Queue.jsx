import { useCallback, useEffect, useState } from 'react';
import { api, enc, post } from './api.js';
import { cat, fieldLabel, hasRole, serviceSummary, text, toValue, when, zipLabel } from './lib.js';
import { ErrorBox, Evidence, Facts, Tag, Values } from './ui.jsx';

const MINE = '__mine';
const STATUSES = ['open', 'claimed', 'snoozed', 'done']; // server uses "done" for resolved tasks

export default function Queue({ user }) {
  const [zips, setZips] = useState(null);
  const [zip, setZip] = useState(MINE);
  const [status, setStatus] = useState('claimed');
  const [tasks, setTasks] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [err, setErr] = useState('');

  const loadZips = useCallback(() => api('/tasks/zips').then(setZips).catch((e) => setErr(e.message)), []);
  const loadTasks = useCallback(() => {
    setTasks(null);
    const q = zip === MINE ? `assignee=me&status=${status}` : `zip=${enc(zip)}&status=${status}`;
    return api('/tasks?' + q).then(setTasks).catch((e) => { setErr(e.message); setTasks([]); });
  }, [zip, status]);

  useEffect(() => { loadZips(); }, [loadZips]);
  useEffect(() => { loadTasks(); }, [loadTasks]);

  const pick = (z) => { setZip(z); setStatus(z === MINE ? 'claimed' : 'open'); setOpenId(null); };
  const refresh = () => { loadZips(); loadTasks(); };

  return (
    <div className="queue">
      <aside className="card zips">
        <button className={'zip' + (zip === MINE ? ' on' : '')} onClick={() => pick(MINE)}>
          <b>My tasks</b><span className="small">claimed by me</span>
        </button>
        <p className="label pad">Areas with open tasks</p>
        {!zips ? <p className="muted pad">Loading…</p> : !zips.length ? <p className="muted pad">The queue is empty.</p> :
          zips.map((z) => (
            <button key={z.zip} className={'zip' + (zip === z.zip ? ' on' : '')} onClick={() => pick(z.zip)}>
              <b>{zipLabel(z.zip)}</b>
              <span className="zip-counts">
                {z.p1 > 0 && <span className="pri p1">{z.p1} urgent</span>}
                <span className="small">{z.open} open</span>
              </span>
            </button>
          ))}
      </aside>

      <section className="card main">
        <ErrorBox>{err}</ErrorBox>
        {openId ? (
          <TaskDetail id={openId} user={user} onBack={() => { setOpenId(null); refresh(); }} onChanged={refresh} />
        ) : (
          <>
            <div className="list-head">
              <h2>{zip === MINE ? 'My tasks' : zipLabel(zip)}</h2>
              <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
                {STATUSES.map((s) => <option key={s}>{s}</option>)}
              </select>
            </div>
            {!tasks ? <p className="muted pad">Loading…</p> : !tasks.length ? (
              <div className="empty"><b>No {status} tasks here</b><p className="muted">{zip === MINE ? 'Pick an area on the left and claim a task.' : 'Try another status or area.'}</p></div>
            ) : tasks.map((t) => (
              <button key={t.task_id} className="task-row" onClick={() => setOpenId(t.task_id)}>
                <span className={'pri p' + t.priority}>P{t.priority}</span>
                <span className="grow">
                  <b>{t.facility?.organization || t.facility?.name}</b>
                  <span className="small">{t.facility?.name} · {t.facility?.address || 'no address'}</span>
                  <span className="tags">{t.categories.map((c) => <Tag key={c} c={c} />)}</span>
                </span>
                <span className="small right">
                  {t.assignee ? (t.assignee === user.email ? 'You' : t.assignee) : `${t.finding_ids.length} findings`}
                  {t.attempts > 0 && <><br />{t.attempts} attempt{t.attempts > 1 ? 's' : ''}</>}
                </span>
              </button>
            ))}
          </>
        )}
      </section>
    </div>
  );
}

const OUTCOMES = [
  ['confirmed', 'Record is right'],
  ['apply', 'Use source value'],
  ['edit', 'Enter correct value'],
  ['dismiss', 'Not a problem'],
  ['closed', 'Place has closed'],
  ['unreachable', 'Couldn’t reach them'],
];

function TaskDetail({ id, user, onBack, onChanged }) {
  const [d, setD] = useState(null);
  const [dec, setDec] = useState({});
  const [method, setMethod] = useState('phone');
  const [notes, setNotes] = useState('');
  const [snooze, setSnooze] = useState(null); // {days, reason} while the form is open
  const [assignee, setAssignee] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');

  const load = useCallback(() => api('/tasks/' + enc(id)).then((x) => { setD(x); setDec({}); }).catch((e) => setErr(e.message)), [id]);
  useEffect(() => { load(); }, [load]);

  const act = async (fn, done) => {
    setBusy(true); setErr(''); setMsg('');
    try { const r = await fn(); if (done) setMsg(done(r)); await load(); onChanged(); } catch (e) { setErr(e.message); }
    setBusy(false);
  };

  if (!d) return <div className="pad"><button className="btn sm" onClick={onBack}>← Back</button><ErrorBox>{err}</ErrorBox>{!err && <p className="muted">Loading…</p>}</div>;

  const mine = d.status === 'claimed' && d.assignee === user.email;
  const heldByOther = d.status === 'claimed' && !mine;
  const canWork = mine || d.status === 'open' || d.status === 'snoozed';
  const open = d.findings.filter((f) => f.status === 'open');
  const chosen = open.filter((f) => dec[f.finding_id]?.outcome);
  const ready = chosen.length > 0 && chosen.every((f) => {
    const x = dec[f.finding_id];
    return x.outcome !== 'edit' || String(x.value ?? '').trim();
  });
  const fac = d.facility || {};
  const firstSvc = Object.values(d.services || {})[0] || {};
  const website = firstSvc.url || firstSvc.organization?.website;

  const resolve = () => act(() => post(`/tasks/${enc(id)}/resolve`, {
    method, notes,
    findings: chosen.map((f) => {
      const x = dec[f.finding_id];
      const out = { finding_id: f.finding_id, outcome: x.outcome };
      if (x.outcome === 'edit') out.value = toValue(f.field, x.value, f.current_value);
      if (x.outcome === 'closed') out.value = x.value || 'defunct';
      if (x.outcome === 'dismiss') out.reason = x.reason || 'checked by specialist';
      return out;
    }),
  }), (r) => (r.open_findings?.length ? `Saved. ${r.open_findings.length} finding(s) still open.` : 'Saved. Every finding is resolved and the record is verified.'));

  return (
    <div className="detail">
      <div className="d-head">
        <div>
          <button className="btn sm" onClick={onBack}>← Back</button>
          <div className="tags" style={{ marginTop: 12 }}>
            <span className={'pri p' + d.priority}>P{d.priority}</span>
            <span className="tag stale">{d.status}{d.assignee ? ` · ${d.assignee === user.email ? 'you' : d.assignee}` : ''}</span>
            {d.lease_until && d.status === 'claimed' && <span className="small">lease until {when(d.lease_until)}</span>}
            {d.attempts > 0 && <span className="small">{d.attempts} unreachable attempt(s)</span>}
          </div>
          <h2>{fac.organization || fac.name}</h2>
          <p className="sub">{fac.name}{fac.description && fac.description !== fac.name ? ` · ${fac.description}` : ''}</p>
        </div>
        <div className="actions">
          {(d.status === 'open' || d.status === 'snoozed') && <button className="btn primary" disabled={busy} onClick={() => act(() => post(`/tasks/${enc(id)}/claim`), () => 'Claimed. It’s yours for 2 hours.')}>Claim task</button>}
          {mine && <button className="btn" disabled={busy} onClick={() => act(() => post(`/tasks/${enc(id)}/release`), () => 'Released back to the queue.')}>Release</button>}
          {canWork && <button className="btn" disabled={busy} onClick={() => setSnooze(snooze ? null : { days: 1, reason: '' })}>Snooze</button>}
        </div>
      </div>

      <div className="d-body">
        <ErrorBox>{err}</ErrorBox>
        {msg && <div className="done">{msg}</div>}
        {heldByOther && <div className="error">Claimed by {d.assignee} until {when(d.lease_until)}. You can read it but not work it.</div>}

        {snooze && (
          <div className="box stack">
            <p className="label">Snooze this task</p>
            <label className="field">Days (1–90)<input type="number" min={1} max={90} value={snooze.days} onChange={(e) => setSnooze({ ...snooze, days: e.target.value })} /></label>
            <label className="field">Reason<input value={snooze.reason} onChange={(e) => setSnooze({ ...snooze, reason: e.target.value })} placeholder="Closed today, call Monday" /></label>
            <button className="btn sm primary" disabled={busy || !(snooze.days >= 1 && snooze.days <= 90)}
              onClick={() => act(() => post(`/tasks/${enc(id)}/snooze`, { days: Number(snooze.days), reason: snooze.reason }), () => { setSnooze(null); return 'Snoozed.'; })}>Snooze</button>
          </div>
        )}

        {hasRole(user.groups, 'lead') && d.status !== 'done' && (
          <div className="inline">
            <label className="field grow">Assign to (lead)<input type="email" value={assignee} onChange={(e) => setAssignee(e.target.value)} placeholder="specialist@example.org" /></label>
            <button className="btn sm" disabled={busy || !assignee} onClick={() => act(() => post(`/tasks/${enc(id)}/assign`, { assignee }), () => `Assigned to ${assignee}.`)}>Assign</button>
          </div>
        )}

        <div className="contact">
          <div>
            <p className="label">Call</p>
            {fac.phones?.length ? fac.phones.map((p) => <a key={p} className="phone" href={'tel:' + p.replace(/[^\d+]/g, '')}>{p}</a>) : <span className="muted">No phone on file</span>}
          </div>
          <div>
            <p className="label">Address</p>
            <span>{fac.address || '—'}</span>
            {(fac.address || fac.latitude) && (
              <a className="small" target="_blank" rel="noopener noreferrer"
                href={'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(fac.latitude ? `${fac.latitude},${fac.longitude}` : fac.address)}>Open map ↗</a>
            )}
          </div>
          {website && <div><p className="label">Website</p><a href={/^https?:/.test(website) ? website : 'http://' + website} target="_blank" rel="noopener noreferrer">{website}</a></div>}
        </div>

        {d.what_to_ask?.length > 0 && (
          <div className="ask">
            <p className="label">Ask on the call</p>
            <ul>{d.what_to_ask.map((q) => <li key={q}>{q}</li>)}</ul>
          </div>
        )}

        {d.memory?.length > 0 && (
          <div>
            <p className="label">Notes from earlier checks</p>
            {d.memory.map((m, i) => <p key={i} className="memo">{m.text}<span className="small"> {m.author} {m.created_at && '· ' + when(m.created_at)}</span></p>)}
          </div>
        )}

        <div className="stack">
          <p className="label">Findings ({open.length} open)</p>
          {d.findings.map((f) => (
            <FindingCard key={f.finding_id} f={f} svc={d.services?.[f.service_id]} dec={dec[f.finding_id]} disabled={!canWork || f.status !== 'open'}
              onChange={(x) => setDec({ ...dec, [f.finding_id]: { ...dec[f.finding_id], ...x } })} />
          ))}
        </div>

        {Object.entries(d.services || {}).map(([sid, h]) => (
          <details key={sid} className="svc">
            <summary>Full record: {h?.name || sid}</summary>
            {h?.description && <p>{h.description}</p>}
            <Facts facts={serviceSummary(h)} flag={new Set(open.filter((f) => f.service_id === sid).map((f) => fieldLabel(f.field)))} />
          </details>
        ))}
      </div>

      {canWork && open.length > 0 && (
        <div className="d-foot">
          <label className="field">How did you check?
            <select value={method} onChange={(e) => setMethod(e.target.value)}>
              <option value="phone">Phone call</option><option value="visit">Visit</option><option value="email">Email</option><option value="web">Website</option>
            </select>
          </label>
          <label className="field grow">Notes<input value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Spoke to the front desk" /></label>
          <button className="btn primary" disabled={busy || !ready} onClick={resolve}>
            {busy ? 'Saving…' : `Resolve ${chosen.length || ''} finding${chosen.length === 1 ? '' : 's'}`}
          </button>
        </div>
      )}
    </div>
  );
}

function FindingCard({ f, svc, dec = {}, disabled, onChange }) {
  const ai = f.model_id && f.model_id !== 'rules';
  const hasProposed = f.proposed_value != null && f.proposed_value !== '';
  const name = 'o-' + f.finding_id;
  return (
    <div className={'finding' + (f.status !== 'open' ? ' closed' : '')}>
      <div className="issue-head">
        <Tag c={f.category} />
        <span className="label">{fieldLabel(f.field)}</span>
        <span className={'tag ' + (ai ? 'ai' : 'stale')}>{ai ? 'AI check' : 'Rule'}</span>
        {f.confidence && <span className="small">{f.confidence} confidence</span>}
        {svc?.name && <span className="small">· {svc.name}</span>}
        {f.status !== 'open' && <span className="tag ok">{f.status}</span>}
      </div>
      <p className="issue-tip">{cat(f.category).tip} {f.note}</p>
      <Values f={f} />
      <Evidence items={f.evidence} />
      {f.what_to_ask?.length > 0 && <p className="small">Ask: {f.what_to_ask.join(' ')}</p>}
      {f.status === 'open' && (
        <fieldset className="opts" disabled={disabled}>
          <legend className="sr">Outcome for {fieldLabel(f.field)}</legend>
          {OUTCOMES.filter(([o]) => o !== 'apply' || hasProposed).map(([o, label]) => (
            <label key={o} className={'opt' + (dec.outcome === o ? ' on' : '')}>
              <input type="radio" name={name} checked={dec.outcome === o} onChange={() => onChange({ outcome: o, value: o === 'edit' ? text(f.proposed_value ?? f.current_value) : o === 'closed' ? 'defunct' : undefined })} />
              <span>{label}</span>
            </label>
          ))}
        </fieldset>
      )}
      {dec.outcome === 'edit' && (
        <label className="field">Correct {fieldLabel(f.field).toLowerCase()}
          <input value={dec.value ?? ''} onChange={(e) => onChange({ value: e.target.value })} />
        </label>
      )}
      {dec.outcome === 'dismiss' && (
        <label className="field">Why isn’t it a problem?
          <input value={dec.reason ?? ''} onChange={(e) => onChange({ reason: e.target.value })} placeholder="Wording only" />
        </label>
      )}
      {dec.outcome === 'closed' && (
        <label className="field">Closed how?
          <select value={dec.value} onChange={(e) => onChange({ value: e.target.value })}>
            <option value="defunct">Permanently (defunct)</option><option value="temporarily closed">Temporarily closed</option>
          </select>
        </label>
      )}
    </div>
  );
}
