import { useState, useCallback } from 'react';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export interface AuditEvent {
  id?: string;
  message_id: string;
  member_id: string;
  member_name: string;
  raw_text: string;
  source: string;
  received_at: string;
  triage?: {
    category: string;
    entities: {
      amount?: number;
      sender_id?: string;
      urls: string[];
      deadline?: string;
      action_demanded?: string;
      biller?: string;
    };
    confidence: number;
    reasoning: string;
    language?: string;
    dlt_verified?: boolean;
    urgency_signals?: string[];
  };
  immunity?: {
    matched: boolean;
    similarity_score: number;
    matched_signature_id?: string;
    matched_semantic_shape?: string;
    hit_count?: number;
    check_time_ms: number;
    match_vector?: 'raw' | 'canonical';
  };
  reputation?: {
    sender_key: string;
    seen_count: number;
    scam_count: number;
    last_verdict?: string;
    is_repeat_offender: boolean;
    lookup_ms: number;
  };
  education_note?: string;
  stage_traces?: Array<{ stage: string; duration_ms: number; detail?: string }>;
  investigation?: {
    verdict: string;
    confidence: number;
    threat_score: number;
    threat_type?: string;
    mode?: 'strands' | 'agentic' | 'fixed' | 'fallback';
    llm_calls?: number;
    agent_trace?: Array<{
      kind: 'thought' | 'tool_call' | 'observation' | 'verdict';
      content?: string;
      tool_name?: string;
      tool_args?: Record<string, unknown>;
      duration_ms?: number;
    }>;
    domain_intel: Array<{
      domain: string;
      age_days?: number;
      registrar?: string;
      tls_age_days?: number;
      tls_issuer?: string;
      redirect_final_url?: string;
      redirect_hops?: number;
      urlhaus_hit: boolean;
      phishtank_hit: boolean;
      google_safe_browsing_hit?: boolean;
      virustotal_positives?: number;
      typosquat_target?: string;
      typosquat_distance?: number;
      homograph_detected?: boolean;
    }>;
    html_analysis?: {
      credential_harvest_score: number;
      has_password_field: boolean;
      has_otp_field: boolean;
      has_aadhaar_field: boolean;
      page_title?: string;
    };
    screenshot_path?: string;
    reasoning: string;
    investigation_time_ms: number;
    tools_used?: string[];
    tools_failed?: string[];
  };
  doer?: {
    task_type: string;
    biller?: string;
    amount?: number;
    due_date?: string;
    plain_text?: string;
    appointment_title?: string;
    appointment_datetime?: string;
    appointment_location?: string;
    reminder_set_for?: string;
    summary?: string;
    complaint?: {
      channel: string;
      category: string;
      victim_name: string;
      suspicious_number?: string;
      suspicious_urls: string[];
      amount_involved?: number;
      money_lost: boolean;
      narrative: string;
      portal_url: string;
      status: string;
    };
  };
  policy?: {
    action: string;
    reason: string;
    rule_matched: string;
    rules_evaluated?: string[];
    escalation_target?: string;
    chakshu_prefilled?: boolean;
    risk_score?: number;
    signals?: string[];
  };
  human_decision?: string;
  total_time_ms: number;
  created_at: string;
}

export interface Stats {
  total_messages: number;
  scams_blocked: number;
  escalations: number;
  immunity_hits: number;
  avg_response_ms: number;
}

export interface ConsentMember {
  member_id: string;
  member_name: string;
  enrolled: boolean;
  enrolled_at: string | null;
  paused_at: string | null;
  consent_scope: string;
}
export interface ConsentStatus {
  members: ConsentMember[];
  monitoring_active: boolean;
  paused_members: string[];
}

export interface IntakeStatus {
  connected: boolean;
  last_message_at: string | null;
  seconds_since_last: number | null;
  messages_last_hour: number;
  by_source: Record<string, number>;
  pipeline_mode: string;
  agent_runtime: string;
}

export interface ImmunityStats {
  total_signatures: number;
  recent_signatures: Array<{
    id: string;
    semantic_shape: string;
    domain_fingerprint?: string;
    created_at: string;
    source_member_id: string;
  }>;
  recent_hits: Array<{
    created_at: string;
    total_time_ms: number;
  }>;
}

export function useAPI() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStats = useCallback(async (): Promise<Stats> => {
    try {
      const res = await fetch(`${API_BASE}/stats`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error('Failed to fetch stats:', err);
      return { total_messages: 0, scams_blocked: 0, escalations: 0, immunity_hits: 0, avg_response_ms: 0 };
    }
  }, []);

  const fetchAudit = useCallback(async (limit = 50, offset = 0): Promise<AuditEvent[]> => {
    try {
      const res = await fetch(`${API_BASE}/audit?limit=${limit}&offset=${offset}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      return data.events || [];
    } catch (err) {
      console.error('Failed to fetch audit:', err);
      return [];
    }
  }, []);

  const fetchImmunityStats = useCallback(async (): Promise<ImmunityStats> => {
    try {
      const res = await fetch(`${API_BASE}/immunity/stats`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error('Failed to fetch immunity stats:', err);
      return { total_signatures: 0, recent_signatures: [], recent_hits: [] };
    }
  }, []);

  const fetchIntakeStatus = useCallback(async (): Promise<IntakeStatus> => {
    try {
      const res = await fetch(`${API_BASE}/intake/status`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch {
      return {
        connected: false, last_message_at: null, seconds_since_last: null,
        messages_last_hour: 0, by_source: {}, pipeline_mode: '?', agent_runtime: '?',
      };
    }
  }, []);

  const fetchMembers = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/members`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      return data.members || [];
    } catch (err) {
      console.error('Failed to fetch members:', err);
      return [];
    }
  }, []);

  const fetchConsent = useCallback(async (): Promise<ConsentStatus> => {
    try {
      const res = await fetch(`${API_BASE}/consent`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch {
      return { members: [], monitoring_active: true, paused_members: [] };
    }
  }, []);

  const setMonitoring = useCallback(async (member_id: string, member_name: string, on: boolean) => {
    const path = on
      ? `/enroll?member_id=${encodeURIComponent(member_id)}&member_name=${encodeURIComponent(member_name)}`
      : `/unenroll?member_id=${encodeURIComponent(member_id)}`;
    try { await fetch(`${API_BASE}${path}`, { method: 'POST' }); } catch { /* ignore */ }
  }, []);

  const sendTestMessage = useCallback(async (raw_text: string, member_name = 'Test User') => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/ingest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          member_id: 'test-member-' + Date.now(),
          member_name,
          raw_text,
          source: 'sms',
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (err: any) {
      setError(err.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  return { fetchStats, fetchAudit, fetchImmunityStats, fetchIntakeStatus, fetchConsent, setMonitoring, fetchMembers, sendTestMessage, loading, error };
}
