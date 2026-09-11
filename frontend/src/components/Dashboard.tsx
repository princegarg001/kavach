import { useEffect, useState, useCallback } from 'react';
import {
  Shield, ShieldCheck, ShieldAlert, Inbox, Zap, Radio, PauseCircle, Power, Lock, ChevronDown,
} from 'lucide-react';
import type { Stats, ImmunityStats, IntakeStatus, ConsentStatus } from '../hooks/useAPI';
import { useAPI } from '../hooks/useAPI';
import { useSSE } from '../hooks/useSSE';
import EventCard from './EventCard';

function useCountUp(value: number, ms = 700) {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (value === n) return;
    const from = n, diff = value - from, start = performance.now();
    let raf = 0;
    const tick = (t: number) => {
      const p = Math.min(1, (t - start) / ms);
      const eased = 1 - Math.pow(1 - p, 3);
      setN(Math.round(from + diff * eased));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return n;
}

function StatCard({ icon: Icon, label, value, suffix = '', color }: {
  icon: any; label: string; value: number; suffix?: string; color: string;
}) {
  const shown = useCountUp(value);
  return (
    <div style={{
      background: 'var(--bg-card)', border: '1px solid var(--border-primary)',
      borderRadius: 'var(--radius-lg)', padding: '16px 18px', flex: '1 1 150px', minWidth: 150,
      boxShadow: 'var(--shadow-sm)', transition: 'box-shadow var(--transition-normal), transform var(--transition-fast)',
    }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = 'var(--shadow-md)'; (e.currentTarget as HTMLElement).style.transform = 'translateY(-1px)'; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = 'var(--shadow-sm)'; (e.currentTarget as HTMLElement).style.transform = 'none'; }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <div style={{
          width: 28, height: 28, borderRadius: 'var(--radius-sm)',
          background: 'var(--bg-sunken)', display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Icon size={15} color={color} />
        </div>
        <span style={{ fontSize: 11.5, color: 'var(--text-tertiary)', fontWeight: 600, letterSpacing: '-0.01em' }}>{label}</span>
      </div>
      <div style={{
        fontSize: 27, fontWeight: 800, color: 'var(--text-primary)',
        fontFamily: 'var(--font-mono)', letterSpacing: '-0.03em',
      }}>
        {shown.toLocaleString()}<span style={{ fontSize: 15, color: 'var(--text-muted)' }}>{suffix}</span>
      </div>
    </div>
  );
}

const FILTERS = [
  { k: 'all', label: 'All' },
  { k: 'scam', label: 'Scam' },
  { k: 'routine_admin', label: 'Routine' },
  { k: 'unknown', label: 'Unknown' },
  { k: 'personal', label: 'Personal' },
];

function ConsentPanel({ consent, onToggle }: {
  consent: ConsentStatus; onToggle: (m: { member_id: string; member_name: string }, on: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  if (!consent.members.length) return null;
  return (
    <div style={{
      background: 'var(--bg-card)', border: '1px solid var(--border-primary)',
      borderRadius: 'var(--radius-lg)', marginBottom: 16, boxShadow: 'var(--shadow-sm)', overflow: 'hidden',
    }}>
      <button onClick={() => setOpen(v => !v)} style={{
        width: '100%', display: 'flex', alignItems: 'center', gap: 9, padding: '12px 16px',
        background: 'transparent', border: 'none', textAlign: 'left',
      }}>
        <Lock size={14} color="var(--text-tertiary)" />
        <span style={{ fontSize: 12.5, fontWeight: 700 }}>Consent &amp; control</span>
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          {consent.monitoring_active ? `${consent.members.length} enrolled` : `${consent.paused_members.length} paused`}
        </span>
        <ChevronDown size={14} color="var(--text-tertiary)" style={{ marginLeft: 'auto', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform var(--transition-fast)' }} />
      </button>
      {open && (
        <div className="fade-in" style={{ padding: '0 16px 14px', borderTop: '1px solid var(--border-primary)' }}>
          {consent.members.map(m => (
            <div key={m.member_id} style={{ padding: '12px 0', borderBottom: '1px solid var(--border-primary)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ fontSize: 12.5, fontWeight: 700 }}>{m.member_name}</span>
                <span style={{
                  fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 999,
                  background: m.enrolled ? 'var(--badge-safe-bg)' : 'var(--badge-unknown-bg)',
                  color: m.enrolled ? 'var(--badge-safe-text)' : 'var(--badge-unknown-text)',
                  border: `1px solid ${m.enrolled ? 'var(--badge-safe-border)' : 'var(--badge-unknown-border)'}`,
                }}>{m.enrolled ? 'Monitoring on' : 'Paused'}</span>
                <button onClick={() => onToggle(m, !m.enrolled)} style={{
                  marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 5,
                  fontSize: 11, fontWeight: 700, padding: '5px 11px', borderRadius: 999,
                  border: `1px solid ${m.enrolled ? 'var(--badge-scam-border)' : 'var(--badge-safe-border)'}`,
                  background: m.enrolled ? 'var(--badge-scam-bg)' : 'var(--badge-safe-bg)',
                  color: m.enrolled ? 'var(--badge-scam-text)' : 'var(--badge-safe-text)',
                }}>
                  <Power size={11} /> {m.enrolled ? 'Kill switch' : 'Resume'}
                </button>
              </div>
              <p style={{ margin: '6px 0 0', fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.5 }}>{m.consent_scope}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const { fetchStats, fetchAudit, fetchImmunityStats, fetchIntakeStatus, fetchConsent, setMonitoring, sendTestMessage } = useAPI();
  const { events, connected, setEvents } = useSSE();
  const [stats, setStats] = useState<Stats>({ total_messages: 0, scams_blocked: 0, escalations: 0, immunity_hits: 0, avg_response_ms: 0 });
  const [immunityStats, setImmunityStats] = useState<ImmunityStats>({ total_signatures: 0, recent_signatures: [], recent_hits: [] });
  const [intake, setIntake] = useState<IntakeStatus | null>(null);
  const [consent, setConsent] = useState<ConsentStatus>({ members: [], monitoring_active: true, paused_members: [] });
  const [testInput, setTestInput] = useState('');
  const [sending, setSending] = useState(false);
  const [filter, setFilter] = useState('all');

  useEffect(() => {
    const load = () => {
      fetchStats().then(setStats);
      fetchImmunityStats().then(setImmunityStats);
      fetchIntakeStatus().then(setIntake);
      fetchConsent().then(setConsent);
    };
    load();
    fetchAudit(50).then((rows) => {
      if (rows.length) setEvents(prev => {
        const seen = new Set(prev.map(e => e.message_id));
        return [...prev, ...rows.filter(e => !seen.has(e.message_id))].slice(0, 200);
      });
    });
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = useCallback(async () => {
    if (!testInput.trim() || sending) return;
    setSending(true);
    await sendTestMessage(testInput.trim());
    setTestInput('');
    setSending(false);
    setTimeout(() => fetchStats().then(setStats), 3000);
  }, [testInput, sending, sendTestMessage, fetchStats]);

  const shown = filter === 'all' ? events : events.filter(e => e.triage?.category === filter);

  const toggleMonitoring = useCallback(async (m: { member_id: string; member_name: string }, on: boolean) => {
    await setMonitoring(m.member_id, m.member_name, on);
    fetchConsent().then(setConsent);
  }, [setMonitoring, fetchConsent]);

  return (
    <div style={{ maxWidth: 1160, margin: '0 auto', padding: '28px 28px 80px' }}>

      {!consent.monitoring_active && (
        <div className="fade-in" style={{
          display: 'flex', alignItems: 'center', gap: 10, padding: '11px 16px', marginBottom: 16,
          background: 'var(--badge-unknown-bg)', border: '1px solid var(--badge-unknown-border)',
          borderRadius: 'var(--radius-lg)', color: 'var(--badge-unknown-text)', fontSize: 12.5, fontWeight: 600,
        }}>
          <PauseCircle size={16} />
          Monitoring paused for {consent.paused_members.join(', ')} — Kavach is not reading their messages.
        </div>
      )}

      {/* Header */}
      <header style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 26, flexWrap: 'wrap' }}>
        <div style={{
          width: 40, height: 40, borderRadius: 'var(--radius-md)',
          background: 'linear-gradient(140deg, var(--accent), var(--accent-purple))',
          display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: 'var(--shadow-glow)',
        }}>
          <Shield size={21} color="#fff" strokeWidth={2.4} />
        </div>
        <div style={{ marginRight: 'auto' }}>
          <h1 style={{ fontSize: 20, fontWeight: 800, letterSpacing: '-0.02em', lineHeight: 1.15 }}>Kavach</h1>
          <p style={{ fontSize: 12, color: 'var(--text-tertiary)' }}>Herd immunity, for a family</p>
        </div>

        {intake && (
          <div title={`${Object.entries(intake.by_source).map(([s, n]) => `${s}: ${n}`).join('  ') || 'no messages yet'}  ·  ${intake.messages_last_hour}/hr  ·  runtime: ${intake.agent_runtime}`}
            style={{
              display: 'flex', alignItems: 'center', gap: 7, padding: '6px 11px', borderRadius: 999,
              background: intake.connected ? 'var(--badge-safe-bg)' : 'var(--bg-sunken)',
              border: `1px solid ${intake.connected ? 'var(--badge-safe-border)' : 'var(--border-primary)'}`,
              fontSize: 11, fontWeight: 700,
              color: intake.connected ? 'var(--badge-safe-text)' : 'var(--text-tertiary)',
            }}>
            <Inbox size={12} />
            {intake.connected
              ? `Intake · ${intake.seconds_since_last != null ? intake.seconds_since_last + 's ago' : 'live'}`
              : 'Intake idle'}
          </div>
        )}

        <div style={{
          display: 'flex', alignItems: 'center', gap: 7, padding: '6px 11px', borderRadius: 999,
          background: connected ? 'var(--badge-safe-bg)' : 'var(--badge-scam-bg)',
          border: `1px solid ${connected ? 'var(--badge-safe-border)' : 'var(--badge-scam-border)'}`,
          fontSize: 11, fontWeight: 700, color: connected ? 'var(--badge-safe-text)' : 'var(--badge-scam-text)',
        }}>
          <Radio size={12} style={{ animation: connected ? 'breathe 2s ease-in-out infinite' : 'none' }} />
          {connected ? 'Live' : 'Offline'}
        </div>
      </header>

      {/* Stats */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <StatCard icon={Inbox} label="Messages" value={stats.total_messages} color="var(--accent-blue)" />
        <StatCard icon={ShieldCheck} label="Scams blocked" value={stats.scams_blocked} color="var(--accent-red)" />
        <StatCard icon={ShieldAlert} label="Escalations" value={stats.escalations} color="var(--accent-amber)" />
        <StatCard icon={Shield} label="Immunity hits" value={stats.immunity_hits} color="var(--accent-green)" />
        <StatCard icon={Zap} label="Avg response" value={stats.avg_response_ms} suffix="ms" color="var(--accent-cyan)" />
      </div>

      <ConsentPanel consent={consent} onToggle={toggleMonitoring} />

      {/* Immunity ledger strip */}
      {immunityStats.total_signatures > 0 && (
        <div style={{
          background: 'var(--bg-card)', border: '1px solid var(--border-primary)',
          borderRadius: 'var(--radius-lg)', padding: '12px 16px', marginBottom: 16,
          display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', boxShadow: 'var(--shadow-sm)',
        }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 12.5, fontWeight: 700 }}>
            <Shield size={15} color="var(--accent-green)" /> Immunity ledger
            <span style={{ color: 'var(--accent-green)', fontFamily: 'var(--font-mono)' }}>{immunityStats.total_signatures}</span>
          </span>
          <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
            {immunityStats.recent_signatures.slice(0, 4).map((s, i) => (
              <span key={i} style={{
                fontSize: 10.5, color: 'var(--text-secondary)', background: 'var(--bg-sunken)',
                border: '1px solid var(--border-primary)', borderRadius: 999, padding: '3px 9px',
                maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>🛡 {s.semantic_shape}</span>
            ))}
          </div>
        </div>
      )}

      {/* Test input */}
      <div style={{
        background: 'var(--bg-card)', border: '1px solid var(--border-primary)',
        borderRadius: 'var(--radius-lg)', padding: 10, marginBottom: 20,
        display: 'flex', gap: 9, boxShadow: 'var(--shadow-sm)',
      }}>
        <input
          value={testInput}
          onChange={(e) => setTestInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="Paste a suspicious message to run it through the agents…"
          style={{
            flex: 1, padding: '9px 12px', background: 'var(--bg-secondary)',
            border: '1px solid var(--border-primary)', borderRadius: 'var(--radius-md)',
            color: 'var(--text-primary)', fontSize: 13, fontFamily: 'var(--font-sans)', outline: 'none',
            transition: 'border-color var(--transition-fast), box-shadow var(--transition-fast)',
          }}
          onFocus={(e) => { e.target.style.borderColor = 'var(--accent)'; e.target.style.boxShadow = '0 0 0 3px var(--accent-glow)'; }}
          onBlur={(e) => { e.target.style.borderColor = 'var(--border-primary)'; e.target.style.boxShadow = 'none'; }}
        />
        <button onClick={submit} disabled={sending || !testInput.trim()} style={{
          padding: '9px 18px', borderRadius: 'var(--radius-md)', border: 'none',
          background: (sending || !testInput.trim()) ? 'var(--bg-sunken)' : 'var(--accent)',
          color: (sending || !testInput.trim()) ? 'var(--text-muted)' : '#fff',
          fontSize: 13, fontWeight: 700, whiteSpace: 'nowrap',
          cursor: (sending || !testInput.trim()) ? 'not-allowed' : 'pointer',
          transition: 'background var(--transition-fast)',
        }}>
          {sending ? 'Analysing…' : 'Analyse'}
        </button>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap', alignItems: 'center' }}>
        {FILTERS.map(f => {
          const on = filter === f.k;
          const count = f.k === 'all' ? events.length : events.filter(e => e.triage?.category === f.k).length;
          return (
            <button key={f.k} onClick={() => setFilter(f.k)} style={{
              padding: '5px 11px', borderRadius: 999, fontSize: 11.5, fontWeight: 700,
              border: `1px solid ${on ? 'var(--accent)' : 'var(--border-primary)'}`,
              background: on ? 'var(--accent-soft)' : 'var(--bg-card)',
              color: on ? 'var(--accent)' : 'var(--text-secondary)',
              transition: 'all var(--transition-fast)',
            }}>
              {f.label}{f.k !== 'all' && <span style={{ opacity: 0.6 }}> {count}</span>}
            </button>
          );
        })}
        <span style={{ marginLeft: 'auto', fontSize: 11.5, color: 'var(--text-muted)' }}>{shown.length} events</span>
      </div>

      {/* Feed */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {shown.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '72px 0', color: 'var(--text-muted)' }}>
            <Shield size={40} style={{ opacity: 0.35, marginBottom: 14 }} />
            <p style={{ fontSize: 15, fontWeight: 600, color: 'var(--text-tertiary)' }}>Nothing yet</p>
            <p style={{ fontSize: 12.5, marginTop: 3 }}>Forward a message to WhatsApp, or paste one above.</p>
          </div>
        ) : shown.map((e, i) => <EventCard key={e.message_id + i} event={e} index={i} />)}
      </div>
    </div>
  );
}
