// Pure helpers (no browser / Vite APIs) so `npm test` can run them under node.

export const ROLES = ['specialist', 'lead', 'admin'];

/** A higher group includes every lower one (specialist < lead < admin). */
export function hasRole(groups, role) {
  const need = ROLES.indexOf(role);
  return (groups || []).some((g) => ROLES.indexOf(g) >= need);
}

export function decodeJwt(token) {
  try {
    const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(decodeURIComponent(escape(atob(part.padEnd(part.length + ((4 - (part.length % 4)) % 4), '=')))));
  } catch {
    return null;
  }
}

/** session = {token} (Cognito) or {devUser, devGroups} (AUTH_MODE=dev). Returns null when signed out / expired. */
export function sessionUser(session, nowSec = Date.now() / 1000) {
  if (!session) return null;
  if (session.devUser) return { email: session.devUser, name: session.devUser, groups: session.devGroups || [] };
  const p = session.token && decodeJwt(session.token);
  if (!p || (p.exp && p.exp <= nowSec)) return null;
  return { email: p.email || p['cognito:username'] || '', name: p.name || p.email || '', groups: p['cognito:groups'] || [], exp: p.exp };
}

/** "ZIP#94044" -> "94044", "COUNTY#Solano" -> "Solano County", "VIRTUAL" -> "Online / phone only". */
export function zipLabel(zip) {
  if (!zip) return '—';
  if (zip.startsWith('ZIP#')) return zip.slice(4);
  if (zip.startsWith('COUNTY#')) return zip.slice(7) + ' County';
  if (zip === 'VIRTUAL') return 'Online / phone only';
  return zip;
}

/** Any HSDS-ish value -> readable text. */
export function text(v) {
  if (v == null || v === '') return '';
  if (Array.isArray(v)) return v.map(text).filter(Boolean).join(', ');
  if (typeof v === 'object') {
    if (v.address_1) return [v.address_1, v.city, [v.state_province, v.postal_code].filter(Boolean).join(' ')].filter(Boolean).join(', ');
    if (v.opens_at || v.byday) return [v.byday, [v.opens_at, v.closes_at].filter(Boolean).join('–')].filter(Boolean).join(' ');
    return v.name || v.description || v.number || v.text || JSON.stringify(v);
  }
  return String(v);
}

/** Fields whose value is a list: an edited "a, b" becomes ["a", "b"]. */
export function toValue(field, typed, original) {
  const isList = Array.isArray(original) || /languages|service_areas|required_documents/.test(field || '');
  return isList ? String(typed).split(',').map((s) => s.trim()).filter(Boolean) : typed;
}

export const CATS = {
  actively_bad:        { label: 'Actively bad', cls: 'bad', tip: 'Site won’t load, the agency looks closed, or its certificate expired.' },
  check_failed:        { label: 'Check failed', cls: 'fail', tip: 'The AI couldn’t reach the site. Not proof the data is wrong.' },
  needs_manual:        { label: 'Needs manual check', cls: 'manual', tip: 'Only a person can confirm this.' },
  new_facility:        { label: 'New facility', cls: 'new', tip: 'Found by the AI, not yet in the directory.' },
  discrepancy:         { label: 'Discrepancy', cls: 'disc', tip: 'Our record and the source disagree.' },
  missing_information: { label: 'Missing information', cls: 'miss', tip: 'Our record is missing this field.' },
  stale:               { label: 'Stale', cls: 'stale', tip: 'Not verified for a long time.' },
  clash:               { label: 'Clash', cls: 'clash', tip: 'A source update conflicts with an earlier human/AI change.' },
  possible_duplicate:  { label: 'Possible duplicate', cls: 'dup', tip: 'Looks like another record.' },
  agent_discovered:    { label: 'Agent discovered', cls: 'new', tip: 'Created by the discovery agent.' },
};
export const cat = (c) => CATS[c] || { label: c || 'Other', cls: 'fail', tip: '' };

const FIELD_LABELS = [
  [/address|location/, 'Address'], [/schedule|hours/, 'Hours'], [/eligib/, 'Eligibility'], [/language/, 'Languages'],
  [/service_area/, 'Area served'], [/access/, 'Accessibility'], [/phone/, 'Phone'], [/website|^url$/, 'Website'],
  [/^status$/, 'Status'], [/description/, 'Description'], [/assured/, 'Last verified'], [/fees/, 'Fees'], [/application/, 'How to apply'],
];
export function fieldLabel(field) {
  const f = (field || '').toLowerCase();
  const hit = FIELD_LABELS.find(([re]) => re.test(f));
  return hit ? hit[1] : field || 'Record';
}

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
export function when(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  let h = d.getHours();
  const ap = h >= 12 ? 'PM' : 'AM';
  h = h % 12 || 12;
  return `${MON[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()} · ${h}:${String(d.getMinutes()).padStart(2, '0')} ${ap}`;
}

/** Flatten an HSDS service document into the fields a specialist reads. */
export function serviceSummary(h) {
  h = h || {};
  const org = h.organization || {};
  const locs = (h.service_at_locations || []).map((s) => s.location).filter(Boolean);
  const addrs = locs.flatMap((l) => l.addresses || []);
  const a = addrs.find((x) => x.address_type === 'physical') || addrs[0];
  const sched = h.schedules || [];
  const hours = [...new Set(sched.map((s) => s.description).filter(Boolean))].join(' ') || sched.map(text).join(', ');
  const phone = [...(h.phones || []), ...(org.phones || [])].find((p) => p.type !== 'fax');
  return {
    Status: h.status || '',
    Address: a ? text(a) : (locs[0] && (locs[0].description || locs[0].name)) || '',
    Phone: phone ? phone.number : '',
    Website: h.url || org.website || '',
    Hours: hours,
    Eligibility: h.eligibility_description || '',
    Fees: h.fees_description || '',
    Languages: text(h.languages),
    'Area served': text(h.service_areas),
    Accessibility: [...new Set(locs.flatMap((l) => l.accessibility || []).map((x) => x.description).filter(Boolean))].join('; '),
  };
}
