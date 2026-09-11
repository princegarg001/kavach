import { useState, useMemo } from 'react';
import type { AuditEvent } from '../hooks/useAPI';
import {
  ShieldCheck, ShieldX, Clock, ChevronDown, ChevronUp,
  CheckCircle2, HelpCircle, Bot, Globe, Lock, Brain, Wrench, Eye,
  Flag, GraduationCap, UserX, FileText, Zap,
} from 'lucide-react';

const CATEGORY = {
  scam:          { label: 'Scam',    v: 'scam' },
  routine_admin: { label: 'Routine', v: 'routine' },
  personal:      { label: 'Personal',v: 'personal' },
  unknown:       { label: 'Unknown', v: 'unknown' },
} as const;

const CAT_ICON: Record<string, any> = {
  scam: ShieldX, routine_admin: Bot, personal: CheckCircle2, unknown: HelpCircle,
};

const ACTION: Record<string, { label: string; color: string; dot: string }> = {
  silent_kill:         { label: 'Blocked silently',  color: 'var(--accent-red)',    dot: 'var(--accent-red)' },
  act:                 { label: 'Handled',           color: 'var(--accent-blue)',   dot: 'var(--accent-blue)' },
  escalate_mother:     { label: 'Asked the family',  color: 'var(--accent-amber)',  dot: 'var(--accent-amber)' },
  escalate_natu:       { label: 'Escalated',         color: 'var(--accent-purple)', dot: 'var(--accent-purple)' },
  escalate_natu_urgent:{ label: 'Urgent escalation', color: 'var(--accent-red)',    dot: 'var(--accent-red)' },
  prepare:             { label: 'Prepared',          color: 'var(--accent-cyan)',   dot: 'var(--accent-cyan)' },
};

// ── Agent reasoning trace ────────────────────────────────────────────────────

const STEP: Record<string, { icon: any; color: string; label: string }> = {
  thought:     { icon: Brain,  color: 'var(--accent-purple)', label: 'Thinking' },
  tool_call:   { icon: Wrench, color: 'var(--accent-blue)',   label: 'Tool' },
  observation: { icon: Eye,    color: 'var(--accent-cyan)',   label: 'Result' },
  verdict:     { icon: Flag,   color: 'var(--accent-amber)',  label: 'Verdict' },
};

function AgentTrace({ trace }: { trace: NonNullable<AuditEvent['investigation']>['agent_trace'] }) {
  if (!trace || trace.length === 0) return null;
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{
        fontSize: 10.5, fontWeight: 700, color: 'var(--text-tertiary)',
        letterSpacing: '0.09em', textTransform: 'uppercase', marginBottom: 12,
        display: 'flex', alignItems: 'center', gap: 6,
      }}>
        <Brain size={12} /> Agent reasoning
        <span style={{ color: 'var(--text-muted)', fontWeight: 500 }}>· {trace.length} steps</span>
      </div>

      <div style={{ position: 'relative', paddingLeft: 26 }}>
        {/* the rail that "draws" down */}
        <div style={{
          position: 'absolute', left: 9, top: 4, bottom: 8, width: 2,
          background: 'linear-gradient(var(--border-active), var(--border-primary))',
          borderRadius: 2, transformOrigin: 'top',
          animation: 'railGrow 0.5s cubic-bezier(0.4,0,0.2,1) both',
        }} />

        {trace.map((step, i) => {
          const cfg = STEP[step.kind] || STEP.thought;
          const Icon = cfg.icon;
          const delay = `${120 + i * 95}ms`;
          const label = step.tool_name || cfg.label;
          const body = step.kind === 'tool_call'
            ? Object.entries(step.tool_args || {}).map(([k, v]) => `${k}: ${v}`).join('  ·  ')
            : (step.content || '');

          return (
            <div key={i} style={{
              position: 'relative', marginBottom: i === trace.length - 1 ? 0 : 14,
              animation: `stepIn 0.4s cubic-bezier(0.16,1,0.3,1) both`, animationDelay: delay,
            }}>
              {/* node */}
              <div style={{
                position: 'absolute', left: -26, top: 0,
                width: 20, height: 20, borderRadius: '50%',
                background: 'var(--bg-card)', border: `1.5px solid ${cfg.color}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                animation: 'nodePop 0.4s cubic-bezier(0.16,1,0.3,1) both', animationDelay: delay,
                boxShadow: '0 0 0 3px var(--bg-card)',
              }}>
                <Icon size={11} color={cfg.color} />
              </div>

              {/* content */}
              <div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
                  <span style={{
                    fontSize: 11.5, fontWeight: 700, color: cfg.color, fontFamily: 'var(--font-mono)',
                    letterSpacing: '-0.01em',
                  }}>
                    {label}
                  </span>
                  {step.duration_ms ? (
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
                      {step.duration_ms}ms
                    </span>
                  ) : null}
                </div>

                {step.kind === 'tool_call' && (
                  <div style={{
                    marginTop: 4, height: 3, borderRadius: 2, overflow: 'hidden',
                    background: 'var(--bg-sunken)',
                  }}>
                    <div style={{
                      height: '100%', width: '45%', borderRadius: 2,
                      background: `linear-gradient(90deg, transparent, ${cfg.color}, transparent)`,
                      backgroundSize: '220% 100%',
                      animation: 'sweep 0.9s ease-out both', animationDelay: delay,
                    }} />
                  </div>
                )}

                {body && (
                  <p style={{
                    margin: '4px 0 0', fontSize: 12, lineHeight: 1.5,
                    color: step.kind === 'verdict' ? 'var(--text-primary)' : 'var(--text-secondary)',
                    fontWeight: step.kind === 'verdict' ? 600 : 400,
                    fontFamily: step.kind === 'observation' ? 'var(--font-mono)' : 'var(--font-sans)',
                    background: step.kind === 'observation' ? 'var(--bg-sunken)' : 'transparent',
                    border: step.kind === 'observation' ? '1px solid var(--border-primary)' : 'none',
                    borderRadius: step.kind === 'observation' ? 'var(--radius-sm)' : 0,
                    padding: step.kind === 'observation' ? '6px 9px' : 0,
                    wordBreak: 'break-word',
                    ...(step.kind === 'verdict' ? {
                      background: 'var(--accent-amber-glow)', padding: '7px 10px',
                      borderRadius: 'var(--radius-sm)', border: '1px solid var(--badge-unknown-border)',
                    } : {}),
                  }}>
                    {body.length > 300 ? body.slice(0, 300) + '…' : body}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Small pieces ────────────────────────────────────────────────────────────

function Chip({ children, tone = 'neutral' }: { children: any; tone?: string }) {
  const map: Record<string, [string, string, string]> = {
    neutral: ['var(--bg-sunken)', 'var(--text-secondary)', 'var(--border-primary)'],
    red:     ['var(--badge-scam-bg)', 'var(--badge-scam-text)', 'var(--badge-scam-border)'],
    green:   ['var(--badge-safe-bg)', 'var(--badge-safe-text)', 'var(--badge-safe-border)'],
    amber:   ['var(--badge-unknown-bg)', 'var(--badge-unknown-text)', 'var(--badge-unknown-border)'],
    cyan:    ['var(--accent-cyan-glow)', 'var(--accent-cyan)', 'var(--border-primary)'],
  };
  const [bg, fg, bd] = map[tone] || map.neutral;
  return (
    <span style={{
      fontSize: 10.5, fontWeight: 600, color: fg, background: bg,
      border: `1px solid ${bd}`, padding: '2px 7px', borderRadius: 999,
      whiteSpace: 'nowrap',
    }}>{children}</span>
  );
}

function RiskMeter({ score }: { score: number }) {
  const color = score >= 70 ? 'var(--accent-red)' : score >= 40 ? 'var(--accent-amber)' : 'var(--accent-green)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
      <div style={{ width: 64, height: 5, borderRadius: 3, background: 'var(--bg-sunken)', overflow: 'hidden' }}>
        <div style={{
          width: `${score}%`, height: '100%', borderRadius: 3, background: color,
          transition: 'width 0.7s cubic-bezier(0.16,1,0.3,1)',
        }} />
      </div>
      <span style={{ fontSize: 10.5, fontWeight: 700, color, fontFamily: 'var(--font-mono)' }}>{score}</span>
    </div>
  );
}

function StageBar({ traces }: { traces: AuditEvent['stage_traces'] }) {
  if (!traces || traces.length === 0) return null;
  const total = traces.reduce((s, t) => s + (t.duration_ms || 0), 0) || 1;
  const palette = ['var(--accent-blue)', 'var(--accent-green)', 'var(--accent-purple)', 'var(--accent-cyan)', 'var(--accent-amber)', 'var(--accent-red)'];
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ display: 'flex', height: 6, borderRadius: 4, overflow: 'hidden', background: 'var(--bg-sunken)' }}>
        {traces.map((t, i) => (
          <div key={i} title={`${t.stage}: ${t.duration_ms}ms`} style={{
            width: `${((t.duration_ms || 0) / total) * 100}%`,
            background: palette[i % palette.length], minWidth: t.duration_ms ? 2 : 0,
          }} />
        ))}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', marginTop: 5 }}>
        {traces.filter(t => t.duration_ms > 0).map((t, i) => (
          <span key={i} style={{ fontSize: 9.5, color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)' }}>
            <span style={{ color: palette[i % palette.length] }}>●</span> {t.stage.replace('stage1_parallel', 'triage+immunity')} {t.duration_ms}ms
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Card ────────────────────────────────────────────────────────────────────

export default function EventCard({ event, index = 0 }: { event: AuditEvent; index?: number }) {
  const [open, setOpen] = useState(false);

  const category = event.triage?.category || 'unknown';
  const cat = (CATEGORY as any)[category] || CATEGORY.unknown;
  const CatIcon = CAT_ICON[category] || HelpCircle;
  const action = event.policy?.action || '';
  const act = ACTION[action] || { label: action || '—', color: 'var(--text-secondary)', dot: 'var(--text-muted)' };
  const immune = !!event.immunity?.matched;
  const inv = event.investigation;

  const timeAgo = useMemo(() => {
    const d = Date.now() - new Date(event.created_at).getTime();
    if (d < 60000) return `${Math.max(1, Math.floor(d / 1000))}s ago`;
    if (d < 3600000) return `${Math.floor(d / 60000)}m ago`;
    if (d < 86400000) return `${Math.floor(d / 3600000)}h ago`;
    return `${Math.floor(d / 86400000)}d ago`;
  }, [event.created_at]);

  return (
    <div
      className="card-in"
      onClick={() => setOpen(v => !v)}
      style={{
        background: 'var(--bg-card)',
        border: `1px solid ${immune ? 'var(--badge-safe-border)' : 'var(--border-primary)'}`,
        borderRadius: 'var(--radius-lg)',
        padding: '15px 18px',
        cursor: 'pointer',
        transition: 'box-shadow var(--transition-normal), border-color var(--transition-normal), transform var(--transition-fast)',
        boxShadow: open ? 'var(--shadow-md)' : 'var(--shadow-sm)',
        animationDelay: `${Math.min(index, 12) * 45}ms`,
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = 'var(--shadow-md)'; (e.currentTarget as HTMLElement).style.borderColor = 'var(--border-active)'; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.boxShadow = open ? 'var(--shadow-md)' : 'var(--shadow-sm)'; (e.currentTarget as HTMLElement).style.borderColor = immune ? 'var(--badge-safe-border)' : 'var(--border-primary)'; }}
    >
      {/* Row 1 — badges + action + time */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{
          display: 'inline-flex', alignItems: 'center', gap: 5,
          padding: '3px 9px', borderRadius: 999, fontSize: 11, fontWeight: 700,
          background: `var(--badge-${cat.v}-bg)`, color: `var(--badge-${cat.v}-text)`,
          border: `1px solid var(--badge-${cat.v}-border)`,
        }}>
          <CatIcon size={12} /> {cat.label}
        </span>

        {immune && (
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 8px', borderRadius: 999,
            fontSize: 10.5, fontWeight: 700, color: 'var(--badge-safe-text)',
            background: 'var(--badge-safe-bg)', border: '1px solid var(--badge-safe-border)',
            animation: 'glowRing 2.4s ease-in-out infinite',
          }}>
            <ShieldCheck size={11} /> Immune · {event.immunity!.check_time_ms}ms
            {event.immunity!.match_vector === 'canonical' && <span style={{ opacity: 0.7 }}> variant</span>}
          </span>
        )}

        {event.reputation?.is_repeat_offender && (
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 4, padding: '3px 8px', borderRadius: 999,
            fontSize: 10.5, fontWeight: 700, color: 'var(--badge-scam-text)',
            background: 'var(--badge-scam-bg)', border: '1px solid var(--badge-scam-border)',
          }}>
            <UserX size={11} /> Repeat offender · {event.reputation.scam_count}×
          </span>
        )}

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
          {event.policy?.risk_score !== undefined && <RiskMeter score={event.policy.risk_score} />}
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11.5, fontWeight: 700, color: act.color, whiteSpace: 'nowrap' }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: act.dot }} /> {act.label}
          </span>
          <span style={{ fontSize: 11, color: 'var(--text-tertiary)', whiteSpace: 'nowrap' }}>{timeAgo}</span>
          {open ? <ChevronUp size={14} color="var(--text-tertiary)" /> : <ChevronDown size={14} color="var(--text-tertiary)" />}
        </div>
      </div>

      {/* Row 2 — meta */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 9, fontSize: 11.5, color: 'var(--text-tertiary)' }}>
        <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>{event.member_name}</span>
        <span>·</span>
        <span style={{ fontFamily: 'var(--font-mono)', textTransform: 'uppercase', fontSize: 10 }}>{event.source}</span>
        {inv?.mode && (
          <>
            <span>·</span>
            <span style={{
              fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 700,
              color: inv.mode === 'strands' ? 'var(--accent-purple)' : 'var(--text-tertiary)',
            }}>
              {inv.mode}{inv.llm_calls ? ` · ${inv.llm_calls} llm` : ''}
            </span>
          </>
        )}
      </div>

      {/* Message */}
      <p style={{
        margin: '9px 0 0', fontSize: 13.5, lineHeight: 1.55, color: 'var(--text-primary)',
        whiteSpace: open ? 'normal' : 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }}>
        {event.raw_text}
      </p>

      {event.triage?.reasoning && (
        <p style={{ margin: '5px 0 0', fontSize: 11.5, color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
          {event.triage.reasoning}
        </p>
      )}

      {/* Education note — always visible */}
      {event.education_note && (
        <div style={{
          display: 'flex', gap: 9, alignItems: 'flex-start', marginTop: 10,
          padding: '9px 12px', background: 'var(--accent-cyan-glow)',
          border: '1px solid var(--border-primary)', borderRadius: 'var(--radius-md)',
        }}>
          <GraduationCap size={15} color="var(--accent-cyan)" style={{ flexShrink: 0, marginTop: 1 }} />
          <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.55, color: 'var(--text-primary)' }}>{event.education_note}</p>
        </div>
      )}

      <StageBar traces={event.stage_traces} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, fontSize: 10.5, color: 'var(--text-muted)' }}>
        <Clock size={11} /> {(event.total_time_ms || 0).toLocaleString()}ms end-to-end
      </div>

      {/* ── Expanded ─────────────────────────────────────────────────────────── */}
      {open && (
        <div className="fade-in" style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--border-primary)' }}>

          {event.triage && (
            <Section title="Triage">
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                <Chip>{(event.triage.confidence * 100).toFixed(0)}% confident</Chip>
                {event.triage.language && event.triage.language !== 'en' && <Chip tone="cyan">🌐 {event.triage.language}</Chip>}
                {event.triage.dlt_verified && <Chip tone="green">DLT-verified sender</Chip>}
                {event.triage.entities?.amount != null && <Chip tone="amber">₹{Number(event.triage.entities.amount).toLocaleString()}</Chip>}
                {event.triage.entities?.action_demanded && <Chip tone="red">wants: {event.triage.entities.action_demanded.replace(/_/g, ' ')}</Chip>}
                {(event.triage.urgency_signals || []).slice(0, 3).map((u, i) => <Chip key={i} tone="red">⚡ {u}</Chip>)}
              </div>
            </Section>
          )}

          {inv && (
            <Section title={`Investigation · ${inv.investigation_time_ms}ms`}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
                <Chip tone={inv.verdict === 'scam' ? 'red' : inv.verdict === 'safe' ? 'green' : 'amber'}>
                  verdict: {inv.verdict} ({(inv.confidence * 100).toFixed(0)}%)
                </Chip>
                <Chip tone={inv.threat_score >= 60 ? 'red' : 'neutral'}>threat {inv.threat_score}/100</Chip>
                {(inv.tools_used || []).map((t, i) => <Chip key={i}>{t}</Chip>)}
              </div>

              <AgentTrace trace={inv.agent_trace} />

              {(inv.domain_intel || []).map((d, i) => (
                <div key={i} style={{
                  marginTop: 8, padding: '9px 12px', background: 'var(--bg-sunken)',
                  borderRadius: 'var(--radius-md)', border: '1px solid var(--border-primary)',
                  display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center',
                }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--accent-blue)', fontWeight: 600 }}>
                    <Globe size={11} style={{ verticalAlign: -1, marginRight: 4 }} />{d.domain}
                  </span>
                  {d.age_days != null && (
                    <Chip tone={d.age_days < 30 ? 'red' : 'neutral'}>📅 {d.age_days}d old</Chip>
                  )}
                  {d.tls_issuer && <span style={{ fontSize: 10.5, color: 'var(--text-tertiary)' }}><Lock size={9} style={{ verticalAlign: -1 }} /> {d.tls_issuer}</span>}
                  {d.urlhaus_hit && <Chip tone="red">URLhaus</Chip>}
                  {d.phishtank_hit && <Chip tone="red">PhishTank</Chip>}
                  {d.google_safe_browsing_hit && <Chip tone="red">GSB unsafe</Chip>}
                  {d.typosquat_target && <Chip tone="amber">imitates {d.typosquat_target}</Chip>}
                  {d.homograph_detected && <Chip tone="red">homograph</Chip>}
                </div>
              ))}

              {inv.html_analysis && inv.html_analysis.credential_harvest_score > 0 && (
                <div style={{ marginTop: 6, fontSize: 11.5, color: 'var(--text-secondary)' }}>
                  🎣 credential-harvest score {inv.html_analysis.credential_harvest_score}
                  {inv.html_analysis.has_password_field && ' · password field'}
                  {inv.html_analysis.has_otp_field && ' · OTP field'}
                  {inv.html_analysis.has_aadhaar_field && ' · Aadhaar field'}
                </div>
              )}

              {inv.screenshot_path && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 6 }}>
                    Sandbox screenshot
                  </div>
                  <img
                    src={`data:image/png;base64,${inv.screenshot_path}`}
                    alt="landing page"
                    style={{ maxWidth: 320, width: '100%', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-active)', boxShadow: 'var(--shadow-sm)' }}
                  />
                </div>
              )}

              {inv.reasoning && (
                <p style={{ margin: '8px 0 0', fontSize: 12, lineHeight: 1.55, color: 'var(--text-secondary)' }}>{inv.reasoning}</p>
              )}
            </Section>
          )}

          {event.doer && (
            <Section title="Agent action">
              <div style={{
                padding: '10px 13px', background: 'var(--accent-blue-glow)',
                border: '1px solid var(--border-primary)', borderRadius: 'var(--radius-md)',
                fontSize: 12.5, color: 'var(--text-primary)', lineHeight: 1.55,
              }}>
                {(event.doer.task_type === 'bank_alert_rewrite' || event.doer.task_type === 'otp_explainer') && event.doer.plain_text && <p style={{ margin: 0 }}>💬 {event.doer.plain_text}</p>}
                {event.doer.task_type === 'bill_due_date' && <p style={{ margin: 0 }}>📅 {event.doer.biller}: ₹{event.doer.amount?.toLocaleString()} due {event.doer.due_date}{event.doer.reminder_set_for && <b style={{ color: 'var(--accent-green)' }}> · reminder set</b>}</p>}
                {event.doer.task_type === 'appointment_reminder' && <p style={{ margin: 0 }}>🗓️ {event.doer.appointment_title} — {event.doer.appointment_datetime}{event.doer.appointment_location ? ` @ ${event.doer.appointment_location}` : ''}{event.doer.reminder_set_for && <b style={{ color: 'var(--accent-green)' }}> · reminder set</b>}</p>}
                {event.doer.task_type === 'complaint_prefill' && event.doer.complaint && (
                  <div>
                    <div style={{ fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6, marginBottom: 5 }}>
                      <FileText size={13} color="var(--accent-cyan)" />
                      {event.doer.complaint.channel === 'cybercrime_1930' ? '1930 cyber-fraud complaint' : 'Chakshu complaint'} · drafted, held for one tap
                    </div>
                    <p style={{ margin: 0, color: 'var(--text-secondary)' }}>{event.doer.complaint.narrative}</p>
                    <p style={{ margin: '5px 0 0', fontSize: 11, color: 'var(--text-tertiary)' }}>Portal: {event.doer.complaint.portal_url} — Kavach never submits this automatically.</p>
                  </div>
                )}
              </div>
            </Section>
          )}

          {event.policy && (
            <Section title="Policy — deterministic">
              <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text-primary)' }}>
                <code>{event.policy.rule_matched}</code>
              </p>
              <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--text-secondary)' }}>{event.policy.reason}</p>
              {(event.policy.signals || []).length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginTop: 7 }}>
                  {event.policy.signals!.map((s, i) => <Chip key={i}>{s}</Chip>)}
                </div>
              )}
              <p style={{ margin: '6px 0 0', fontSize: 10.5, color: 'var(--text-muted)' }}>
                {event.policy.rules_evaluated?.length || 0} rules evaluated · the agents investigate, code decides
              </p>
            </Section>
          )}

          {event.human_decision && (
            <div style={{
              marginTop: 12, padding: '9px 13px', borderRadius: 'var(--radius-md)',
              fontSize: 12.5, fontWeight: 700, display: 'flex', alignItems: 'center', gap: 7,
              background: event.human_decision === 'safe' ? 'var(--badge-safe-bg)' : 'var(--badge-scam-bg)',
              color: event.human_decision === 'safe' ? 'var(--badge-safe-text)' : 'var(--badge-scam-text)',
              border: `1px solid ${event.human_decision === 'safe' ? 'var(--badge-safe-border)' : 'var(--badge-scam-border)'}`,
            }}>
              <Zap size={13} /> Human decision: {event.human_decision.toUpperCase()}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: any }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{
        fontSize: 10, fontWeight: 700, color: 'var(--text-tertiary)',
        letterSpacing: '0.09em', textTransform: 'uppercase', marginBottom: 7,
      }}>{title}</div>
      {children}
    </div>
  );
}
