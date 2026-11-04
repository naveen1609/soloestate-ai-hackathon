'use client';
import { useEffect, useState } from 'react';
import {
  decideCampaignPlan,
  decideContentItem,
  decideCreativeAsset,
  fetchGrowthProperties,
  orchestrateKit,
} from '../../lib/growth-engine/api';
import {
  ItemStatus,
  KitSummary,
  MarketingBrief,
  OrchestrationStep,
  PropertyItem,
  SegmentedFeature,
} from '../../lib/growth-engine/types';
import ApprovalExportPanel from './approval-export-panel';
import CampaignPlannerPanel from './campaign-planner-panel';
import ContentStudioPanel from './content-studio-panel';
import CreativeStudioPanel from './creative-studio-panel';
import VisualIntelligencePanel from './visual-intelligence-panel';
import VoiceBriefPanel from './voice-brief-panel';

export default function GrowthWorkspace() {
  const [properties, setProperties] = useState<PropertyItem[]>([]);
  const [selectedPropertyId, setSelectedPropertyId] = useState<string>('');
  const [demoPresets, setDemoPresets] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Workflow step: 0 = Brief, 1 = Visual, 2 = Creative, 3 = Copy, 4 = Strategy, 5 = Export
  const [activeStep, setActiveStep] = useState<number>(0);

  // Brief state
  const [brief, setBrief] = useState<MarketingBrief>({
    property_id: '',
    input_mode: 'voice',
    raw_input: '',
    target_audience: 'Working tech professionals and expanding families in Bengaluru',
    key_selling_points: ['Prime connectivity', 'Convenient neighborhood access', 'Spacious floorplan'],
    tone: 'warm',
    campaign_objective: 'lead_generation',
    special_offers: '',
  });

  // Selected image & SAM features
  const [selectedImageIndex, setSelectedImageIndex] = useState<number>(0);
  const [segmentedFeatures, setSegmentedFeatures] = useState<SegmentedFeature[]>([]);

  // Generated Kit State
  const [currentKit, setCurrentKit] = useState<KitSummary | null>(null);
  const [orchestrating, setOrchestrating] = useState(false);
  const [stepsExecuted, setStepsExecuted] = useState<OrchestrationStep[]>([]);

  useEffect(() => {
    async function loadData() {
      try {
        const res = await fetchGrowthProperties();
        setProperties(res.properties);
        setDemoPresets(res.demo_voice_briefs || []);
        if (res.properties.length > 0) {
          const first = res.properties[0];
          setSelectedPropertyId(first.id);
          setBrief((prev) => ({
            ...prev,
            property_id: first.id,
            raw_input: res.demo_voice_briefs?.[0]?.transcript || 'Spacious 2 BHK in prime Indiranagar with morning balcony light and modular kitchen.',
          }));
        }
      } catch (err: any) {
        setError(err?.message || 'Failed to load properties');
      } finally {
        setLoading(false);
      }
    }
    void loadData();
  }, []);

  const activeProperty = properties.find((p) => p.id === selectedPropertyId) || properties[0];

  const handlePropertyChange = (newId: string) => {
    setSelectedPropertyId(newId);
    setBrief((prev) => ({ ...prev, property_id: newId }));
    setSelectedImageIndex(0);
    setSegmentedFeatures([]);
    setCurrentKit(null);
    setStepsExecuted([]);
  };

  const handleRunOrchestration = async () => {
    if (!selectedPropertyId) return;
    setOrchestrating(true);
    setError('');
    try {
      const selectedPhoto = activeProperty?.images?.[selectedImageIndex];
      const res = await orchestrateKit({
        property_id: selectedPropertyId,
        brief: {
          ...brief,
          property_id: selectedPropertyId,
        },
        selected_image_ids: selectedPhoto ? [selectedPhoto.id] : [],
        channels: ['instagram_feed', 'instagram_story', 'facebook_feed', 'festival_offer'],
        generate_creative: true,
        generate_plan: true,
      });
      setCurrentKit(res.kit);
      setStepsExecuted(res.steps_executed);
      // Automatically advance to Copy Studio to view results
      setActiveStep(3);
    } catch (err: any) {
      setError(err?.message || 'Orchestration failed');
    } finally {
      setOrchestrating(false);
    }
  };

  const handleDecideContent = async (
    itemId: string,
    decision: {
      status: ItemStatus;
      expected_version?: number;
    template_id?: string;
      headline?: string;
      body?: string;
      call_to_action?: string;
      hashtags?: string[];
      approval_notes?: string;
    }
  ) => {
    if (!currentKit) return;
    try {
      const item = currentKit.content_items.find((c) => c.id === itemId);
      const versionToPass = decision.expected_version ?? item?.version ?? 1;
      const updated = await decideContentItem(currentKit.id, itemId, {
        ...decision,
        expected_version: versionToPass,
      });
      setCurrentKit((prev) =>
        prev
          ? {
              ...prev,
              content_items: prev.content_items.map((c) => (c.id === itemId ? updated : c)),
            }
          : null
      );
      return updated;
    } catch (err: any) {
      setError(err?.message || 'Failed to update content item');
      throw err;
    }
  };

  const handleDecideCreative = async (assetId: string, status: ItemStatus) => {
    if (!currentKit) return;
    try {
      const asset = currentKit.creative_assets.find((cr) => cr.id === assetId);
      const versionToPass = asset?.version ?? 1;
      const updated = await decideCreativeAsset(currentKit.id, assetId, {
        status,
        expected_version: versionToPass,
      });
      setCurrentKit((prev) =>
        prev
          ? {
              ...prev,
              creative_assets: prev.creative_assets.map((cr) => (cr.id === assetId ? updated : cr)),
            }
          : null
      );
      return updated;
    } catch (err: any) {
      setError(err?.message || 'Failed to update creative asset');
      throw err;
    }
  };

  const handleDecidePlan = async (status: ItemStatus) => {
    if (!currentKit || !currentKit.campaign_plan) return;
    try {
      const versionToPass = currentKit.campaign_plan.version ?? 1;
      const updated = await decideCampaignPlan(currentKit.id, {
        plan_id: currentKit.campaign_plan.id,
        status,
        expected_version: versionToPass,
      });
      setCurrentKit((prev) =>
        prev
          ? {
              ...prev,
              campaign_plan: updated,
            }
          : null
      );
      return updated;
    } catch (err: any) {
      setError(err?.message || 'Failed to update campaign plan');
      throw err;
    }
  };

  const handleApproveAll = async () => {
    if (!currentKit) return;
    setError('');
    let kitCopy = { ...currentKit };
    try {
      const updatedContent = [...kitCopy.content_items];
      for (let i = 0; i < updatedContent.length; i++) {
        const item = updatedContent[i];
        if (item.status === 'draft') {
          const updated = await decideContentItem(kitCopy.id, item.id, {
            status: 'approved',
            expected_version: item.version,
          });
          updatedContent[i] = updated;
        }
      }
      const updatedCreatives = [...kitCopy.creative_assets];
      for (let i = 0; i < updatedCreatives.length; i++) {
        const cr = updatedCreatives[i];
        if (cr.status === 'draft') {
          const updated = await decideCreativeAsset(kitCopy.id, cr.id, {
            status: 'approved',
            expected_version: cr.version,
          });
          updatedCreatives[i] = updated;
        }
      }
      let updatedPlan = kitCopy.campaign_plan;
      if (kitCopy.campaign_plan?.status === 'draft') {
        updatedPlan = await decideCampaignPlan(kitCopy.id, {
          plan_id: kitCopy.campaign_plan.id,
          status: 'approved',
          expected_version: kitCopy.campaign_plan.version,
        });
      }
      setCurrentKit({
        ...kitCopy,
        content_items: updatedContent,
        creative_assets: updatedCreatives,
        campaign_plan: updatedPlan,
      });
    } catch (err: any) {
      setError(err?.message || 'Failed to approve all items');
    }
  };

  if (loading) {
    return <div className="ge-card"><p role="status">Loading Growth Engine Workspace…</p></div>;
  }

  return (
    <div className="ge-container">
      {/* Top Header & Property Picker */}
      <div className="ge-header-bar">
        <div className="ge-header-title">
          <span className="eyebrow">MODULAR MARKETING INTELLIGENCE</span>
          <h1>Growth Engine-lite</h1>
          <p>Multi-model property marketing intelligence with grounding verification and owner approval gates.</p>
        </div>

        <div className="ge-property-selector">
          <label style={{ fontSize: '12px', fontWeight: 700, color: '#475569' }}>Selected Property:</label>
          <select
            value={selectedPropertyId}
            onChange={(e) => handlePropertyChange(e.target.value)}
          >
            {properties.map((p) => (
              <option key={p.id} value={p.id}>
                {p.title} ({p.bedrooms} BHK • {p.locality} • ₹{(Number(p.price) / 10000000).toFixed(2)} Cr)
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && <p className="notice notice-error" role="alert">{error}</p>}

      {/* One Click Pipeline Orchestrator Banner */}
      <div className="ge-wonder-banner">
        <div className="ge-wonder-info">
          <h3>🪄 Growth Engine Local Pipeline Orchestrator</h3>
          <p>
            Executes full pipeline: Voice Brief $\rightarrow$ SAM 3.1 Feature Detection $\rightarrow$ Muse Image Concepts $\rightarrow$ Muse Spark Grounded Copy & Strategy.
          </p>
        </div>
        <button
          type="button"
          className="ge-wonder-btn"
          onClick={handleRunOrchestration}
          disabled={orchestrating}
        >
          {orchestrating ? '⏳ Orchestrating Pipeline...' : '🚀 One-Click Orchestrate Kit'}
        </button>
      </div>

      {/* Stepper Navigation */}
      <div className="ge-stepper">
        <button
          type="button"
          className={`ge-step-btn ${activeStep === 0 ? 'active' : ''}`}
          onClick={() => setActiveStep(0)}
        >
          <span className="ge-step-num">1</span>
          <div className="ge-step-label">
            <span className="ge-step-title">Voice Brief</span>
            <span className="ge-step-sub">Muse Voice</span>
          </div>
        </button>

        <button
          type="button"
          className={`ge-step-btn ${activeStep === 1 ? 'active' : ''}`}
          onClick={() => setActiveStep(1)}
        >
          <span className="ge-step-num">2</span>
          <div className="ge-step-label">
            <span className="ge-step-title">Visual Intelligence</span>
            <span className="ge-step-sub">Meta SAM 3.1</span>
          </div>
        </button>

        <button
          type="button"
          className={`ge-step-btn ${activeStep === 2 ? 'active' : ''}`}
          onClick={() => setActiveStep(2)}
        >
          <span className="ge-step-num">3</span>
          <div className="ge-step-label">
            <span className="ge-step-title">Creative Studio</span>
            <span className="ge-step-sub">Muse Image</span>
          </div>
        </button>

        <button
          type="button"
          className={`ge-step-btn ${activeStep === 3 ? 'active' : ''}`}
          onClick={() => setActiveStep(3)}
        >
          <span className="ge-step-num">4</span>
          <div className="ge-step-label">
            <span className="ge-step-title">Content Studio</span>
            <span className="ge-step-sub">Muse Spark Copy</span>
          </div>
        </button>

        <button
          type="button"
          className={`ge-step-btn ${activeStep === 4 ? 'active' : ''}`}
          onClick={() => setActiveStep(4)}
        >
          <span className="ge-step-num">5</span>
          <div className="ge-step-label">
            <span className="ge-step-title">Campaign Strategy</span>
            <span className="ge-step-sub">Muse Spark Planner</span>
          </div>
        </button>

        <button
          type="button"
          className={`ge-step-btn ${activeStep === 5 ? 'active' : ''}`}
          onClick={() => setActiveStep(5)}
        >
          <span className="ge-step-num">6</span>
          <div className="ge-step-label">
            <span className="ge-step-title">Approval & Export</span>
            <span className="ge-step-sub">Owner Gates</span>
          </div>
        </button>
      </div>

      {/* Execution Trace Stepper */}
      {stepsExecuted.length > 0 && (
        <div style={{ background: '#f8fafc', padding: '14px 18px', borderRadius: '10px', border: '1px solid #e2e8f0' }}>
          <div style={{ fontSize: '12px', fontWeight: 700, color: '#475569', marginBottom: '8px' }}>
            ⚡ Growth Engine Pipeline Orchestration Trace ({stepsExecuted.length} steps executed):
          </div>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {stepsExecuted.map((step, idx) => (
              <div
                key={idx}
                style={{
                  background: step.status === 'completed' ? '#dcfce7' : '#fee2e2',
                  color: step.status === 'completed' ? '#15803d' : '#991b1b',
                  padding: '4px 10px',
                  borderRadius: '6px',
                  fontSize: '11px',
                  fontWeight: 600,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px',
                }}
                title={step.details}
              >
                <span>{step.status === 'completed' ? '✓' : '⚠️'}</span>
                <span>{step.step_name}</span>
                <span style={{ opacity: 0.7, fontSize: '10px' }}>({step.duration_ms}ms)</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Active Panel View */}
      {activeStep === 0 && (
        <VoiceBriefPanel
          propertyId={selectedPropertyId}
          brief={brief}
          onBriefChange={setBrief}
          demoPresets={demoPresets}
        />
      )}

      {activeStep === 1 && (
        <VisualIntelligencePanel
          propertyId={selectedPropertyId}
          images={activeProperty?.images || []}
          selectedImageIndex={selectedImageIndex}
          onSelectImage={setSelectedImageIndex}
          features={segmentedFeatures}
          onFeaturesDetected={setSegmentedFeatures}
        />
      )}

      {activeStep === 2 && (
        <CreativeStudioPanel
          creatives={currentKit?.creative_assets || []}
          onDecideCreative={handleDecideCreative}
        />
      )}

      {activeStep === 3 && (
        <ContentStudioPanel
          contentItems={currentKit?.content_items || []}
          onDecideContent={handleDecideContent}
        />
      )}

      {activeStep === 4 && (
        <CampaignPlannerPanel
          plan={currentKit?.campaign_plan}
          onDecidePlan={handleDecidePlan}
        />
      )}

      {activeStep === 5 && (
        <ApprovalExportPanel
          kit={currentKit || undefined}
          onApproveAll={handleApproveAll}
        />
      )}
    </div>
  );
}
