import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, enc, post } from './api.js';
import { CATS, fieldLabel, serviceSummary, text, toValue, when } from './lib.js';
import { ErrorBox, Evidence, Facts, Tag, Values } from './ui.jsx';

// Record review, ported from UI_prototype.html: list flagged records, open one, decide each finding.
export default function Records({ user }) {
  const [tab, setTab] = useState('unverified');
  const [source, setSource] = useState('');
  const [zip, setZip] = useState('');
  const [q, setQ] = useState('');
  const [category, setCategory] = useState('');
  const [rows, setRows] = useState(null);
  const [findings, setFindings] = useState([]);
  const [openId, setOpenId] = useState(null);
  const [err, setErr] = useState('');

  const load = useCallback(async () => {
    setRows(null); setErr('');
    const f = (source ? `&source=${enc(source)}` : '') + (zip ? `&zip=${enc(zip)}` : '');
    try {
      // /records and /findings scan the whole table: always filter server-side as much as possible.
      const [r, fs] = await Promise.all([
        api(`/records?status=${tab}&limit=500${f}`),
        tab === 'unverified' ? api(`/findings?status=open${f}`) : Promise.resolve([]),
      ]);
      setRows(r); setFindings(fs);
    } catch (e) { setErr(e.message); setRows([]); }
  }, [tab, source, zip]);
  useEffect(() => { load(); }, [load]);

  const bySvc = useMemo(() => {
    const m = {};
    findings.forEach((f) => (m[f.service_id] = m[f.service_id] || []).push(f));
    return m;
  }, [findings]);

  const visible = (rows || []).filter((r) => {
    const cats = (bySvc[r.service_id] || []).map((f) => f.category).concat(r.unverified_reason || []);
    const hay = `${r.org_name} ${r.service_name} ${r.service_id}`.toLowerCase();
    return (!category || cats.includes(category)) && (!q || hay.includes(q.toLowerCase()));
  });

  return (
    <section className="card">
      <div className="toolbar">
        <div className="tabs" role="tablist">
          {['unverified', 'verified'].map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} className={'tab' + (tab === t ? ' on' : '')} onClick={() => setTab(t)}>
              {t[0].toUpperCase() + t.slice(1)}{tab === t && rows ? ` (${rows.length})` : ''}
            </button>
          ))}
        </div>
        <div className="tools">
          <input aria-label="Search" placeholder="Search name or ID" value={q} onChange={(e) => setQ(e.target.value)} />
          <input aria-label="ZIP" placeholder="ZIP" value={zip} onChange={(e) => setZip(e.target.value.trim())} style={{ width: 90 }} />
          <select aria-label="Source" value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">All sources</option><option value="ndp">NDP</option><option value="odrs">ODRS</option><option value="agent-discovery">Agent discovery</option>
          </select>
          {tab === 'unverified' && (
            <select aria-label="Problem" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All problems</option>
              {Object.entries(CATS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          )}
          <button className="btn sm" onClick={load}>Reload</button>
        </div>
      </div>
      <ErrorBox>{err}</ErrorBox>
      {!rows ? <p className="muted pad">Loading…</p> : !visible.length ? (
        <div className="empty"><b>Nothing to show</b><p className="muted">No records match this tab and filter.</p></div>
      ) : (
        <>
          {visible.map((r) => {
            const fs = bySvc[r.service_id] || [];
            const cats = [...new Set(fs.map((f) => f.category))];
            return (
              <button key={r.service_id} className="task-row" onClick={() => setOpenId(r.service_id)}>
                <span className="grow">
                  <b>{r.org_name || '—'}</b>
                  <span className="small">{r.service_name} · {r.primary_zip || 'no ZIP'} · {r.source}</span>
                  <span className="tags">
                    {cats.length ? cats.map((c) => <Tag key={c} c={c} />) : r.unverified_reason ? <Tag c={r.unverified_reason} /> : <span className="tag ok">Verified</span>}
                    {fs.length > 0 && <span className="small">{[...new Set(fs.map((f) => fieldLabel(f.field)))].join(' · ')}</span>}
                  </span>
                </span>
                <span className="small right">checked {when(r.last_check_at || r.last_seen_at)}<br />{(r.verified_by || r.updated_by || '').replace(/^(user|source):/, '')}</span>
              </button>
            );
          })}
          {rows.length === 500 && <p className="muted pad">Showing the first 500. Narrow by ZIP or source to see others.</p>}
        </>
      )}
      {openId && <RecordDialog id={openId} user={user} onClose={() => setOpenId(null)} onSaved={load} />}
    </section>
  );
}

function RecordDialog({ id, onClose, onSaved }) {
  const [rec, setRec] = useState(null);
  const [dec, setDec] = useState({}); // finding_id -> {choice: keep|web|edit, text}
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

  const save = async () => {
    setBusy(true); setErr(''); setMsg('');
    const errors = [];
    const label = (f) => fieldLabel(f.field);
    // 1) changes first
    for (const f of open) {
      const d = dec[f.finding_id];
      try {
        if (d.choice === 'web') await post(`/findings/${enc(f.finding_id)}/apply`);
        if (d.choice === 'edit') await post(`/findings/${enc(f.finding_id)}/edit`, { value: toValue(f.field, d.text ?? '', f.current_value) });
      } catch (e) { errors.push(`${label(f)}: ${e.message}`); }
    }
    // 2) then "keep". A change re-checks the record server-side, which can replace an open finding,
    //    so look each one up again by id, else by category + field.
    const today = new Date().toISOString().slice(0, 10);
    for (const f of open.filter((x) => dec[x.finding_id].choice === 'keep')) {
      let now = [];
      try { now = ((await api('/records/' + enc(id))).findings || []).filter((x) => x.status === 'open'); } catch { /* use none */ }
      const g = now.find((x) => x.finding_id === f.finding_id) || now.find((x) => x.category === f.category && x.field === f.field);
      if (!g) continue;
      try {
        if (g.category === 'stale') await post(`/findings/${enc(g.finding_id)}/edit`, { value: today });
        else await post(`/findings/${enc(g.finding_id)}/dismiss`, { reason: 'kept current by specialist' });
      } catch (e) { errors.push(`${label(f)}: ${e.message}`); }
    }
    setDec({});
    await load();
    onSaved();
    if (errors.length) setErr('Some changes were not saved. ' + errors.join(' · '));
    else setMsg('Saved under your name.');
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
                <div className="box"><span>ID</span><b className="mono">{id}</b></div>
                <div className="box"><span>Status</span><b>{idx.verification_status}{idx.verified_by ? ` · ${idx.verified_by.replace(/^user:/, '')}` : ''}</b></div>
              </div>
              {h?.description && <div><p className="label">Description</p><p>{h.description}</p></div>}
              <Facts facts={serviceSummary(h)} flag={new Set(open.map((f) => fieldLabel(f.field)))} />
              {open.length > 0 ? (
                <div className="stack">
                  <p className="label">What to do</p>
                  {open.map((f) => <Decide key={f.finding_id} f={f} d={dec[f.finding_id] || {}} set={(x) => setDec({ ...dec, [f.finding_id]: { ...dec[f.finding_id], ...x } })} />)}
                </div>
              ) : <div className="done">No open problems.</div>}
            </>
          )}
        </div>
        <div className="d-foot">
          <button className="btn" onClick={recheck} disabled={busy}>Re-check with AI</button>
          <span className="grow" />
          {open.length > 0 && !allDecided && <span className="small">Choose an option for every problem to confirm.</span>}
          {open.length > 0 && <button className="btn primary" disabled={busy || !allDecided} onClick={save}>{busy ? 'Saving…' : 'Confirm'}</button>}
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
