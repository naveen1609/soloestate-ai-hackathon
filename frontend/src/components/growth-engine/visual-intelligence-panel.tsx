'use client';
import { useState } from 'react';
import { segmentVisualFeatures } from '../../lib/growth-engine/api';
import { PropertyImage, SegmentedFeature } from '../../lib/growth-engine/types';

interface VisualIntelligencePanelProps {
  propertyId: string;
  images: PropertyImage[];
  selectedImageIndex: number;
  onSelectImage: (idx: number) => void;
  features: SegmentedFeature[];
  onFeaturesDetected: (features: SegmentedFeature[]) => void;
}

export default function VisualIntelligencePanel({
  propertyId,
  images,
  selectedImageIndex,
  onSelectImage,
  features,
  onFeaturesDetected,
}: VisualIntelligencePanelProps) {
  const [analyzing, setAnalyzing] = useState(false);
  const [summary, setSummary] = useState('');
  const [error, setError] = useState('');

  const activeImage = images[selectedImageIndex] || images[0];

  const handleRunSAM = async () => {
    if (!activeImage) return;
    setAnalyzing(true);
    setError('');
    try {
      const res = await segmentVisualFeatures({
        property_id: propertyId,
        image_id: activeImage.id,
      });
      onFeaturesDetected(res.features);
      setSummary(res.architectural_summary);
    } catch (err: any) {
      setError(err?.message || 'SAM 3.1 segmentation failed');
    } finally {
      setAnalyzing(false);
    }
  };

  return (
    <div className="ge-grid-2">
      <div className="ge-card">
        <div className="ge-card-header">
          <h2>📸 Authentic Property Photography</h2>
          <span className="ge-status-pill approved">Preserved Untouched</span>
        </div>

        {activeImage ? (
          <div>
            <div className="ge-image-stage" style={{ position: 'relative' }}>
              <img src={activeImage.image_url} alt={activeImage.caption || 'Authentic Property Photo'} />
              <div className="ge-authentic-badge">✓ Authentic Photograph</div>

              {/* Bounding box overlays from SAM 3.1 */}
              {features.map((feat, idx) => {
                if (!feat.bounding_box) return null;
                const [ymin, xmin, ymax, xmax] = feat.bounding_box;
                return (
                  <div
                    key={idx}
                    style={{
                      position: 'absolute',
                      top: `${ymin * 100}%`,
                      left: `${xmin * 100}%`,
                      width: `${(xmax - xmin) * 100}%`,
                      height: `${(ymax - ymin) * 100}%`,
                      border: '2px solid #38bdf8',
                      backgroundColor: 'rgba(56, 189, 248, 0.25)',
                      pointerEvents: 'none',
                    }}
                    title={`${feat.label} (${(feat.confidence * 100).toFixed(0)}%)`}
                  >
                    <span
                      style={{
                        position: 'absolute',
                        top: '-20px',
                        left: '0',
                        background: '#0284c7',
                        color: 'white',
                        fontSize: '10px',
                        padding: '1px 6px',
                        borderRadius: '3px',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {feat.label}
                    </span>
                  </div>
                );
              })}
            </div>

            <p style={{ marginTop: '10px', fontSize: '13px', color: '#475569' }}>
              <strong>Caption:</strong> {activeImage.caption || 'Verified property photo'}
            </p>

            {/* Thumbnail selector */}
            {images.length > 1 && (
              <div style={{ display: 'flex', gap: '10px', marginTop: '14px', overflowX: 'auto' }}>
                {images.map((img, idx) => (
                  <button
                    key={img.id}
                    type="button"
                    onClick={() => onSelectImage(idx)}
                    style={{
                      border: idx === selectedImageIndex ? '2px solid #16a34a' : '1px solid #cbd5e1',
                      borderRadius: '6px',
                      overflow: 'hidden',
                      padding: 0,
                      cursor: 'pointer',
                      width: '72px',
                      height: '52px',
                    }}
                  >
                    <img src={img.image_url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          <p style={{ color: '#64748b' }}>No authentic photos attached to this property.</p>
        )}
      </div>

      <div className="ge-card">
        <div className="ge-card-header">
          <h2>🔍 Meta SAM 3.1 Visual Intelligence</h2>
          <button
            type="button"
            className="button button-light"
            onClick={handleRunSAM}
            disabled={analyzing || !activeImage}
          >
            {analyzing ? 'Segmenting...' : '✨ Run SAM 3.1 Segmentation'}
          </button>
        </div>

        <p style={{ fontSize: '13px', color: '#64748b' }}>
          Meta SAM 3.1 segments verifiable architectural and layout attributes directly from authentic photos to feed creative prompts without visual misrepresentation.
        </p>

        {error && <p className="notice notice-error">{error}</p>}

        {summary && (
          <div style={{ background: '#f0fdf4', padding: '12px', borderRadius: '8px', border: '1px solid #bbf7d0', fontSize: '13px', color: '#166534', margin: '12px 0' }}>
            {summary}
          </div>
        )}

        <div className="ge-feature-tag-list">
          {features.length > 0 ? (
            features.map((feat, idx) => (
              <div key={idx} className="ge-feature-tag-item">
                <div>
                  <strong>{feat.label}</strong>
                  <p style={{ margin: '2px 0 0', fontSize: '12px', color: '#64748b' }}>{feat.description}</p>
                </div>
                <span>{(feat.confidence * 100).toFixed(0)}% verified</span>
              </div>
            ))
          ) : (
            <div style={{ padding: '24px', textAlign: 'center', color: '#94a3b8', border: '1px dashed #cbd5e1', borderRadius: '8px' }}>
              Click <strong>&quot;Run SAM 3.1 Segmentation&quot;</strong> to detect architectural elements from the photograph.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
