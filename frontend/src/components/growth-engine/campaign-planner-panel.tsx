'use client';
import { CampaignPlan, ItemStatus } from '../../lib/growth-engine/types';

interface CampaignPlannerPanelProps {
  plan?: CampaignPlan;
  onDecidePlan: (status: ItemStatus) => void;
}

export default function CampaignPlannerPanel({
  plan,
  onDecidePlan,
}: CampaignPlannerPanelProps) {
  if (!plan) {
    return (
      <div className="ge-card">
        <div className="ge-card-header">
          <h2>📊 Meta Muse Spark Campaign Planner</h2>
          <span className="ge-status-pill draft">Strategy Planning</span>
        </div>
        <div style={{ padding: '36px', textAlign: 'center', color: '#94a3b8', border: '1px dashed #cbd5e1', borderRadius: '10px' }}>
          No campaign plan generated yet. Run the <strong>&quot;Growth Engine One-Click Pipeline&quot;</strong> above!
        </div>
      </div>
    );
  }

  const spec = plan.target_audience_spec || {};
  const geo = spec.geographic_targeting || { city: 'Bengaluru', center_locality: 'Prime Locality', radius_km: 10 };
  const demo = spec.demographics || { age_range: [28, 55], income_band: 'High Net Worth' };
  const interests = spec.interests || ['Real Estate', 'Luxury Homes'];

  return (
    <div className="ge-card">
      <div className="ge-card-header">
        <h2>📊 Campaign Planning</h2>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <span className={`ge-status-pill ${plan.status}`}>{plan.status}</span>
          <span className="ge-status-pill draft" style={{ background: '#ede9fe', color: '#6d28d9' }}>
            Guardrail Constrained
          </span>
        </div>
      </div>

      <div style={{ background: '#fef3c7', border: '1px solid #fde68a', padding: '12px 16px', borderRadius: '8px', color: '#92400e', fontSize: '13px', marginBottom: '18px' }}>
        ⚠️ <strong>Architectural Boundary Notice:</strong> Content & strategy approval NEVER authorizes live advertising spend or external Meta API publishing in this MVP.
      </div>

      <div className="ge-grid-2">
        <div>
          <h3 style={{ fontSize: '15px', margin: '0 0 10px', color: '#0f172a' }}>🎯 Audience & Geographic Targeting</h3>
          <div style={{ background: '#f8fafc', padding: '16px', borderRadius: '8px', border: '1px solid #e2e8f0', fontSize: '13px', display: 'grid', gap: '8px' }}>
            <div><strong>Location:</strong> {geo.center_locality}, {geo.city} ({geo.radius_km} km radius)</div>
            <div><strong>Age Demographic:</strong> {demo.age_range?.[0]} – {demo.age_range?.[1]} years</div>
            <div><strong>Income Segment:</strong> {demo.income_band}</div>
            <div>
              <strong>Target Interests:</strong>
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '4px' }}>
                {interests.map((int: string, idx: number) => (
                  <span key={idx} style={{ background: '#e2e8f0', padding: '2px 8px', borderRadius: '12px', fontSize: '11px' }}>
                    {int}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div>
          <h3 style={{ fontSize: '15px', margin: '0 0 10px', color: '#0f172a' }}>💰 Recommended Budget & Placements</h3>
          <div style={{ background: '#f8fafc', padding: '16px', borderRadius: '8px', border: '1px solid #e2e8f0', fontSize: '13px', display: 'grid', gap: '8px' }}>
            <div>
              <strong>Daily Budget:</strong> <span style={{ fontSize: '16px', fontWeight: 700, color: '#16a34a' }}>₹{Number(plan.suggested_daily_budget).toLocaleString()} / day</span>
            </div>
            <div><strong>Duration:</strong> {plan.suggested_duration_days} days sprint</div>
            <div><strong>Total Estimated Spend Cap:</strong> ₹{(Number(plan.suggested_daily_budget) * plan.suggested_duration_days).toLocaleString()}</div>
            <div>
              <strong>Recommended Placements:</strong>
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '4px' }}>
                {plan.recommended_placements.map((pl: string, idx: number) => (
                  <span key={idx} style={{ background: '#dbeafe', color: '#1e40af', padding: '2px 8px', borderRadius: '12px', fontSize: '11px', fontWeight: 600 }}>
                    {pl}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div style={{ marginTop: '16px', background: '#f1f5f9', padding: '14px', borderRadius: '8px', fontSize: '13px', color: '#334155' }}>
        <strong>Planner Rationale:</strong> {plan.rationale}
      </div>

      <div style={{ display: 'flex', gap: '10px', marginTop: '20px', justifyContent: 'flex-end' }}>
        <button
          type="button"
          className="button"
          style={{ background: '#16a34a', color: 'white', border: 'none', padding: '10px 18px' }}
          onClick={() => onDecidePlan('approved')}
        >
          ✓ Approve Campaign Strategy
        </button>
        <button
          type="button"
          className="button button-light"
          style={{ padding: '10px 18px' }}
          onClick={() => onDecidePlan('rejected')}
        >
          ✕ Reject Strategy
        </button>
      </div>
    </div>
  );
}
