'use client';
import { useState } from 'react';
import { exportKit } from '../../lib/growth-engine/api';
import { ExportPackage, KitSummary } from '../../lib/growth-engine/types';

interface ApprovalExportPanelProps {
  kit?: KitSummary;
  onApproveAll: () => void;
}

export default function ApprovalExportPanel({
  kit,
  onApproveAll,
}: ApprovalExportPanelProps) {
  const [exportPackage, setExportPackage] = useState<ExportPackage | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState('');

  if (!kit) {
    return (
      <div className="ge-card">
        <div className="ge-card-header">
          <h2>📦 Owner Approval & Asset Export</h2>
        </div>
        <p style={{ color: '#64748b' }}>Generate a kit first to review and export.</p>
      </div>
    );
  }

  const handleExport = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await exportKit(kit.id);
      setExportPackage(res);
    } catch (err: any) {
      setError(err?.message || 'Export failed');
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = () => {
    if (!exportPackage) return;
    navigator.clipboard.writeText(JSON.stringify(exportPackage, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const approvedContentCount = kit.content_items.filter((c) => c.status === 'approved').length;
  const approvedCreativeCount = kit.creative_assets.filter((c) => c.status === 'approved').length;

  return (
    <div className="ge-card">
      <div className="ge-card-header">
        <h2>📦 Owner Approval & Asset Export Workspace</h2>
        <span className={`ge-status-pill ${kit.status}`}>{kit.status}</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px', marginBottom: '20px' }}>
        <div style={{ background: '#f8fafc', padding: '16px', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
          <span style={{ fontSize: '12px', color: '#64748b' }}>Content Drafts</span>
          <div style={{ fontSize: '22px', fontWeight: 700, color: '#0f172a' }}>
            {approvedContentCount} / {kit.content_items.length} Approved
          </div>
        </div>
        <div style={{ background: '#f8fafc', padding: '16px', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
          <span style={{ fontSize: '12px', color: '#64748b' }}>Creative Concepts</span>
          <div style={{ fontSize: '22px', fontWeight: 700, color: '#0f172a' }}>
            {approvedCreativeCount} / {kit.creative_assets.length} Approved
          </div>
        </div>
        <div style={{ background: '#f8fafc', padding: '16px', borderRadius: '8px', border: '1px solid #e2e8f0' }}>
          <span style={{ fontSize: '12px', color: '#64748b' }}>Campaign Strategy</span>
          <div style={{ fontSize: '22px', fontWeight: 700, color: '#0f172a' }}>
            {kit.campaign_plan?.status === 'approved' ? '✓ Approved' : 'Draft'}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '20px' }}>
        <button
          type="button"
          className="button"
          style={{ background: '#16a34a', color: 'white', border: 'none' }}
          onClick={onApproveAll}
        >
          ✓ Approve Entire Marketing Kit
        </button>
        <button
          type="button"
          className="button button-light"
          onClick={handleExport}
          disabled={loading}
        >
          {loading ? 'Exporting...' : '📥 Generate Export Package'}
        </button>
      </div>

      {error && <p className="notice notice-error">{error}</p>}

      {exportPackage && (
        <div style={{ marginTop: '20px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
            <strong>Exported Marketing Package ({exportPackage.property_title})</strong>
            <button
              type="button"
              className="button button-light"
              style={{ fontSize: '12px', padding: '4px 10px' }}
              onClick={handleCopy}
            >
              {copied ? '✓ Copied to Clipboard!' : '📋 Copy JSON'}
            </button>
          </div>
          <div className="ge-export-box">
            {JSON.stringify(exportPackage, null, 2)}
          </div>
          <p style={{ fontSize: '12px', color: '#64748b', marginTop: '10px' }}>
            {exportPackage.safety_disclaimer}
          </p>
        </div>
      )}
    </div>
  );
}
