import { useCallback, useEffect, useRef, useState } from 'react';
import { api, enc, post } from './api.js';
import { CATS, fieldLabel, serviceSummary, text, toValue, when } from './lib.js';
import { ErrorBox, Evidence, Facts, Tag, Values } from './ui.jsx';

const PAGE = 20; // the server reads the status index newest-first; never pull the whole table

// Record review: list flagged records 20 at a time, open one, decide each finding.
export default function Records({ user }) {
  const [tab, setTab] = useState('unverified');
  const [q, setQ] = useState('');
  const [zip, setZip] = useState('');
  const [typed, setTyped] = useState({ q: '', zip: '' }); // q / zip once typing pauses
  const [reason, setReason] = useState('');
  const [rows, setRows] = useState(null);
  const [next, setNext] = useState(null);
  const [more, setMore] = useState(false);
  const [openId, setOpenId] = useState(null);
  const [err, setErr] = useState('');
  const latest = useRef(0); // only the newest request may fill the list (switching tabs fast mixed them up)

  useEffect(() => {
    const t = setTimeout(() => setTyped({ q: q.trim(), zip: zip.trim() }), 350);
    return () => clearTimeout(t);
  }, [q, zip]);

  const fetchPage = useCallback(async (cursor) => {
    const id = ++latest.current;
    const p = new URLSearchParams({ status: tab, limit: PAGE });
    if (typed.q) p.set('q', typed.q);
    if (typed.zip) p.set('zip', typed.zip);
    if (tab === 'unverified' && reason) p.set('reason', reason);
    if (cursor) p.set('cursor', cursor);
    try {
      const { data, next: n } = await api('/records?' + p, { withNext: true });
      if (id !== latest.current) return;
      setRows((old) => (cursor ? [...(old || []), ...data] : data));
      setNext(n);
      setErr('');
    } catch (e) {
      if (id !== latest.current) return;
      setErr('Could not load records: ' + e.message);
      if (!cursor) setRows([]);
    }
  }, [tab, typed, reason]);

  const reload = useCallback(() => { setRows(null); setNext(null); fetchPage(null); }, [fetchPage]);
  useEffect(() => { reload(); }, [reload]);
  const loadMore = async () => { setMore(true); await fetchPage(next); setMore(false); };

  return (
    <section className="card">
      <div className="toolbar">
        <div className="tabs" role="tablist">
          {['unverified', 'verified'].map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} className={'tab' + (tab === t ? ' on' : '')} onClick={() => setTab(t)}>
              {t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
        <div className="tools">
          <input aria-label="Search" placeholder="Search by name" value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} />
          <input aria-label="ZIP" placeholder="ZIP" value={zip} onChange={(e) => setZip(e.target.value)} style={{ width: 90 }} />
          {tab === 'unverified' && (
            <select aria-label="Main problem" value={reason} onChange={(e) => setReason(e.target.value)}>
              <option value="">All problems</option>
              {Object.entries(CATS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          )}
          <button className="btn sm" onClick={reload}>Reload</button>
        </div>
      </div>
      <ErrorBox>{err}</ErrorBox>
      {!rows ? <p className="muted pad">Loading…</p> : (
        <>
          {!rows.length && (
            <div className="empty">
              <b>{next ? 'No matches yet' : 'Nothing to show'}</b>
              <p className="muted">{next ? 'None in the records checked so far.' : 'No records match this tab and filter.'}</p>
            </div>
          )}
          {rows.map((r) => (
            <button key={r.service_id} className="task-row" onClick={() => setOpenId(r.service_id)}>
              <span className="grow">
                <b>{r.org_name || '—'}</b>
                <span className="small">{r.service_name} · {r.primary_zip || 'no ZIP'}</span>
                <span className="tags">
                  {r.verification_status === 'verified' ? <span className="tag ok">Verified</span>
                    : r.unverified_reason ? <Tag c={r.unverified_reason} /> : <span className="tag stale">Unverified</span>}
                  {r.verification_status !== 'verified' && r.open_flag_count > 0 && (
                    <span className="small">{r.open_flag_count} open problem{r.open_flag_count === 1 ? '' : 's'}</span>
                  )}
                </span>
              </span>
              <span className="small right">
                {r.verification_status === 'verified' ? `verified ${when(r.verified_at || r.last_check_at)}` : `updated ${when(r.last_seen_at)}`}
                <br />{[r.verified_by, r.updated_by].find((x) => x?.startsWith('user:'))?.slice(5) || (r.verified_by?.startsWith('agent:') ? 'AI check' : '')}
              </span>
            </button>
          ))}
          {next && (
            <div className="pad" style={{ textAlign: 'center' }}>
              <button className="btn" onClick={loadMore} disabled={more}>{more ? 'Loading…' : rows.length ? 'Load more' : 'Keep looking'}</button>
            </div>
          )}
        </>
      )}
      {openId && <RecordDialog id={openId} user={user} onClose={() => setOpenId(null)} onSaved={reload} />}
    </section>
  );
}

function RecordDialog({ id, onClose, onSaved }) {
  const [rec, setRec] = useState(null);
  const [dec, setDec] = useState({}); // finding_id -> {choice: keep|web|edit, text}
  const [method, setMethod] = useState('web');
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');

  const load = useCallback(() => api('/records/' + enc(id)).then(setRec).catch((e) => setErr('Could not load this record: ' + e.message)), [id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const k = (e) => e.key === 'Escape' && onClose();
    document.addEventListener('keydown', k);
    return () => document.removeEventListener('keydown', k);
  }, [onClose]);

  const open = (rec?.findings || []).filter((f) => f.status === 'open');
  const allDecided = open.length > 0 && open.every((f) => dec[f.finding_id]?.choice);
  const idx = rec?.index || {};

  // One call: the server applies every decision and marks the record verified when nothing is left open.
  const save = async () => {
    setBusy(true); setErr(''); setMsg('');
    try {
      const out = await post(`/records/${enc(id)}/resolve`, {
        method, notes,
        findings: open.map((f) => {
          const d = dec[f.finding_id];
          if (d.choice === 'web') return { finding_id: f.finding_id, outcome: 'apply' };
          if (d.choice === 'edit') return { finding_id: f.finding_id, outcome: 'edit', value: toValue(f.field, d.text ?? '', f.current_value) };
          return f.category === 'stale' ? { finding_id: f.finding_id, outcome: 'confirmed' }
            : { finding_id: f.finding_id, outcome: 'dismiss', reason: 'kept current by specialist' };
        }),
      });
      setDec({});
      setMsg(out.open_findings.length ? `Saved. ${out.open_findings.length} problem(s) still open.` : 'Verified and saved under your name.');
      onSaved();
    } catch (e) { setErr('Not saved: ' + e.message); }
    await load();
    setBusy(false);
  };

  const recheck = async () => {
    if (!window.confirm('Re-check this record with the AI agent? This is a paid run (about $0.5–2) and takes ~1.5 minutes.')) return;
    try { await post('/verify/' + enc(id)); setMsg('Re-check queued. Reload the record in a couple of minutes to see the result.'); }
    catch (e) { setErr('Re-check failed: ' + e.message); }
  };

  const h = rec?.hsds;
  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby="dTitle">
        <div className="d-head">
          <div>
            <div className="tags">{open.map((f) => f.category).filter((c, i, a) => a.indexOf(c) === i).map((c) => <Tag key={c} c={c} />)}</div>
            <h2 id="dTitle">{idx.org_name || h?.organization?.name || '…'}</h2>
            <p className="sub">{idx.service_name || h?.name}</p>
          </div>
          <button className="btn sm" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="d-body">
          <ErrorBox>{err}</ErrorBox>
          {msg && <div className="done">{msg}</div>}
          {!rec ? !err && <p className="muted">Loading record…</p> : (
            <>
              <div className="two">
                <div className="box"><span>Status</span><b>{idx.verification_status}{idx.verified_by ? ` · ${idx.verified_by.replace(/^(user|agent):/, '')}` : ''}</b></div>
                <div className="box"><span>ZIP code</span><b>{idx.primary_zip || '—'}</b></div>
              </div>
              {h?.description && <div><p className="label">Description</p><p>{h.description}</p></div>}
              <Facts facts={serviceSummary(h)} flag={new Set(open.map((f) => fieldLabel(f.field)))} />
              {open.length > 0 ? (
                <div className="stack">
                  <p className="label">What to do</p>
                  {open.map((f) => <Decide key={f.finding_id} f={f} d={dec[f.finding_id] || {}} set={(x) => setDec({ ...dec, [f.finding_id]: { ...dec[f.finding_id], ...x } })} />)}
                </div>
              ) : idx.verification_status === 'verified' ? <div className="done">Verified. No open problems.</div>
                : <div className="ask">No open problems, but the record isn’t verified yet. Check it, then press <b>Mark verified</b>.</div>}
            </>
          )}
        </div>
        <div className="d-foot">
          <button className="btn" onClick={recheck} disabled={busy}>Re-check with AI</button>
          {rec && (open.length > 0 || idx.verification_status !== 'verified') && (
            <>
              <label className="field">How did you check?
                <select value={method} onChange={(e) => setMethod(e.target.value)}>
                  <option value="web">Website</option><option value="phone">Phone call</option><option value="email">Email</option><option value="visit">Visit</option>
                </select>
              </label>
              <label className="field grow">Notes<input value={notes} maxLength={2000} onChange={(e) => setNotes(e.target.value)} placeholder="Matches the agency website" /></label>
              {open.length > 0 && !allDecided && <span className="small">Choose an option for every problem.</span>}
              <button className="btn primary" disabled={busy || (open.length > 0 && !allDecided)} onClick={save}>
                {busy ? 'Saving…' : open.length ? 'Confirm' : 'Mark verified'}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Decide({ f, d, set }) {
  const stale = f.category === 'stale';
  const hasProposed = f.proposed_value != null && f.proposed_value !== '';
  const opt = (choice, title, value) => (
    <button className={'opt' + (d.choice === choice ? ' on' : '')} aria-pressed={d.choice === choice}
      onClick={() => set({ choice, text: choice === 'edit' && d.text == null ? text(f.proposed_value ?? f.current_value) : d.text })}>
      <span className="t">{title}</span>{value != null && <span className="v">{value || '(empty)'}</span>}
    </button>
  );
  return (
    <div className="finding">
      <div className="issue-head"><Tag c={f.category} /><span className="label">{fieldLabel(f.field)}</span>
        <span className={'tag ' + (f.model_id && f.model_id !== 'rules' ? 'ai' : 'stale')}>{f.model_id && f.model_id !== 'rules' ? 'AI check' : 'Rule'}</span></div>
      {f.note && <p className="issue-tip">{f.note}</p>}
      <Values f={f} />
      <Evidence items={f.evidence} />
      <div className="opts">
        {opt('keep', stale ? 'Still correct: mark as checked today' : 'Keep our record', stale ? null : text(f.current_value))}
        {hasProposed && opt('web', 'Use source value', text(f.proposed_value))}
        {opt('edit', 'Type a new value', null)}
      </div>
      {d.choice === 'edit' && (
        <label className="field">New {fieldLabel(f.field).toLowerCase()}<input value={d.text ?? ''} onChange={(e) => set({ text: e.target.value })} /></label>
      )}
    </div>
  );
}
