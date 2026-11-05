import { cat, text, when } from './lib.js';

export const Tag = ({ c }) => <span className={'tag ' + cat(c).cls} title={cat(c).tip}>{cat(c).label}</span>;

export const ErrorBox = ({ children }) => (children ? <div className="error" role="alert">{children}</div> : null);

export function Evidence({ items }) {
  if (!items?.length) return null;
  return (
    <div className="evidence">
      <p className="label">Evidence</p>
      {items.map((e, i) => (
        <div key={i} className="ev">
          {e.url && <a href={e.download_url || e.url} target="_blank" rel="noopener noreferrer">{e.url}</a>}
          {e.snippet && <q>{e.snippet}</q>}
          {e.fetched_at && <span className="small">fetched {when(e.fetched_at)}</span>}
        </div>
      ))}
    </div>
  );
}

/** Label/value rows; `flag` = labels to highlight as possibly wrong. */
export function Facts({ facts, flag = new Set() }) {
  return (
    <div className="details">
      {Object.entries(facts).filter(([k, v]) => v || flag.has(k)).map(([k, v]) => (
        <div key={k} className={'d' + (flag.has(k) ? ' flag' : '')}>
          <span>{k}</span>
          <div>
            <span>{k === 'Website' && v ? <a href={/^https?:\/\//.test(v) ? v : 'http://' + v} target="_blank" rel="noopener noreferrer">{v}</a> : v || '—'}</span>
            {k === 'Address' && v && <a className="small" href={'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(v)} target="_blank" rel="noopener noreferrer">Open in Google Maps ↗</a>}
            {flag.has(k) && <span className="flagnote">May be wrong, check it</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

/** current vs proposed values of a finding. */
export function Values({ f }) {
  return (
    <div className="two">
      <div className="box"><span>Our record</span><b>{text(f.current_value) || '(empty)'}</b></div>
      {f.proposed_value != null && f.proposed_value !== '' && (
        <div className="box proposed"><span>Source says</span><b>{text(f.proposed_value)}</b></div>
      )}
    </div>
  );
}
