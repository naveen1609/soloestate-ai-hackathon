'use client';
import { CreativeAsset, ItemStatus } from '../../lib/growth-engine/types';

interface CreativeStudioPanelProps {
  creatives: CreativeAsset[];
  onDecideCreative: (assetId: string, status: ItemStatus) => void;
}

export default function CreativeStudioPanel({
  creatives,
  onDecideCreative,
}: CreativeStudioPanelProps) {
  return (
    <div className="ge-card">
      <div className="ge-card-header">
        <h2>🎨 Meta Muse Image Creative Studio</h2>
        <span className="ge-status-pill draft">Truth-in-Advertising Guard</span>
      </div>

      <p style={{ fontSize: '13px', color: '#64748b', marginBottom: '20px' }}>
        Meta Muse Image generates marketing creatives and social cards grounded in verified features. Generated concepts are watermarked and never presented as authentic photographs.
      </p>

      {creatives.length > 0 ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '20px' }}>
          {creatives.map((cr) => (
            <div
              key={cr.id}
              style={{
                border: '1px solid #cbd5e1',
                borderRadius: '12px',
                overflow: 'hidden',
                background: '#f8fafc',
                display: 'flex',
                flexDirection: 'column',
              }}
            >
              <div className="ge-image-stage" style={{ minHeight: '260px', position: 'relative' }}>
                <img src={cr.image_url} alt="Creative concept visual" style={{ width: '100%', height: 'auto' }} />
                <div className="ge-concept-badge">⚠️ {cr.concept_watermark}</div>
              </div>

              <div style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '10px', flex: 1 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '12px', fontWeight: 700, color: '#0f172a', textTransform: 'capitalize' }}>
                    Type: {cr.asset_type.replace('_', ' ')}
                  </span>
                  <span className={`ge-status-pill ${cr.status}`}>{cr.status}</span>
                </div>

                <div style={{ background: '#f1f5f9', padding: '10px', borderRadius: '6px', fontSize: '11px', color: '#475569' }}>
                  <strong>Prompt Hash:</strong> <code>{cr.prompt_hash.slice(0, 16)}...</code><br />
                  <strong>Model:</strong> {cr.model_id}
                </div>

                <div style={{ marginTop: 'auto', display: 'flex', gap: '8px', paddingTop: '10px' }}>
                  <button
                    type="button"
                    className="button"
                    style={{ flex: 1, background: '#16a34a', color: 'white', border: 'none', padding: '8px' }}
                    onClick={() => onDecideCreative(cr.id, 'approved')}
                  >
                    ✓ Approve Concept
                  </button>
                  <button
                    type="button"
                    className="button button-light"
                    style={{ flex: 1, padding: '8px' }}
                    onClick={() => onDecideCreative(cr.id, 'rejected')}
                  >
                    ✕ Reject
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ padding: '36px', textAlign: 'center', color: '#94a3b8', border: '1px dashed #cbd5e1', borderRadius: '10px' }}>
          No creative concepts generated yet. Run the <strong>&quot;Growth Engine One-Click Pipeline&quot;</strong> above to generate grounded visual concepts!
        </div>
      )}
    </div>
  );
}
