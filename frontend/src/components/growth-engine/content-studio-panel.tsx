'use client';
import { useState } from 'react';
import { ChannelType, ContentItem, ItemStatus } from '../../lib/growth-engine/types';

interface ContentStudioPanelProps {
  contentItems: ContentItem[];
  onDecideContent: (
    itemId: string,
    decision: {
      status: ItemStatus;
      template_id?: string;
      approval_notes?: string;
    }
  ) => void;
}

export default function ContentStudioPanel({
  contentItems,
  onDecideContent,
}: ContentStudioPanelProps) {
  const [selectedChannel, setSelectedChannel] = useState<ChannelType>('instagram_feed');
  const [template, setTemplate] = useState('standard_residence');
  const activeItem = contentItems.find((item) => item.channel === selectedChannel) || contentItems[0];

  const channelLabels: Record<ChannelType, string> = {
    instagram_feed: '📷 Instagram Feed',
    instagram_story: '📱 Instagram Story',
    facebook_feed: '👥 Facebook Post',
    festival_offer: '🪔 Festive Campaign',
  };

  return (
    <div className="ge-card">
      <div className="ge-card-header">
        <h2>✍️ Meta Muse Spark Copy Studio</h2>
        <span className="ge-status-pill approved">CRM Grounded</span>
      </div>

      <p style={{ fontSize: '13px', color: '#64748b', marginBottom: '16px' }}>
        Platform-tailored drafts verified against official CRM records. The server renders bedrooms, locality, and price from CRM. Save a template choice to preview a new draft.
      </p>

      {contentItems.length > 0 ? (
        <div>
          {/* Channel Selector Tabs */}
          <div className="ge-channel-tabs">
            {contentItems.map((item) => (
              <button
                key={item.channel}
                type="button"
                className={`ge-channel-tab ${selectedChannel === item.channel ? 'active' : ''}`}
                onClick={() => {
                  setSelectedChannel(item.channel);
                  setTemplate("standard_residence");
                }}
              >
                {channelLabels[item.channel] || item.channel}
                <span style={{ marginLeft: '6px', fontSize: '11px', opacity: 0.8 }}>({item.status})</span>
              </button>
            ))}
          </div>

          {activeItem && (
            <div style={{ display: 'grid', gap: '16px', background: '#f8fafc', padding: '20px', borderRadius: '10px', border: '1px solid #e2e8f0' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                <span className={`ge-grounding-pill ${activeItem.grounding_verified ? 'verified' : 'discrepancy'}`}>
                  {activeItem.grounding_verified ? '✓ Grounding Verified' : '⚠️ Grounding Discrepancy'}
                </span>
                <span style={{ fontSize: '12px', color: '#64748b' }}>
                  {activeItem.verification_notes}
                </span>
                <span className={`ge-status-pill ${activeItem.status}`}>{activeItem.status}</span>
              </div>

              <div>
                <label style={{ fontSize: '12px', fontWeight: 600, color: '#475569' }}>Headline / Hook</label>
                <input
                  type="text"
                  value={activeItem.headline}
                  readOnly
                  style={{ fontWeight: 600 }}
                />
              </div>

              <div>
                <label style={{ fontSize: '12px', fontWeight: 600, color: '#475569' }}>Caption / Body Copy</label>
                <textarea
                  value={activeItem.body}
                  readOnly
                  rows={6}
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
                <div>
                  <label style={{ fontSize: '12px', fontWeight: 600, color: '#475569' }}>Call-To-Action (CTA)</label>
                  <input
                    type="text"
                    value={activeItem.call_to_action}
                    readOnly
                  />
                </div>

                <div>
                  <label style={{ fontSize: '12px', fontWeight: 600, color: '#475569' }}>Hashtags (Comma separated)</label>
                  <input
                    type="text"
                    value={activeItem.hashtags.join(', ')}
                    readOnly
                  />
                </div>
              </div>

              <label>
                Copy template
                <select value={template} onChange={(event) => setTemplate(event.target.value)}>
                  <option value="standard_residence">Standard residence</option>
                  <option value="compact_story">Compact story</option>
                  <option value="detailed_showcase">Property showcase</option>
                  <option value="festive_spotlight">Festive spotlight</option>
                </select>
              </label>
              <button type="button" className="button button-light"
                onClick={() => onDecideContent(activeItem.id, { status: 'draft', template_id: template })}>
                Save and preview template
              </button>
              <div style={{ display: 'flex', gap: '10px', marginTop: '10px', justifyContent: 'flex-end' }}>
                <button
                  type="button"
                  className="button"
                  style={{ background: '#16a34a', color: 'white', border: 'none', padding: '10px 18px' }}
                  onClick={() => onDecideContent(activeItem.id, { status: 'approved' })}
                >
                  ✓ Approve Content Item
                </button>
                <button
                  type="button"
                  className="button button-light"
                  style={{ padding: '10px 18px' }}
                  onClick={() => onDecideContent(activeItem.id, { status: 'rejected' })}
                >
                  ✕ Reject Draft
                </button>
              </div>
            </div>
          )}
        </div>
      ) : (
        <div style={{ padding: '36px', textAlign: 'center', color: '#94a3b8', border: '1px dashed #cbd5e1', borderRadius: '10px' }}>
          No content drafts generated yet. Run the <strong>&quot;Growth Engine One-Click Pipeline&quot;</strong> above!
        </div>
      )}
    </div>
  );
}
