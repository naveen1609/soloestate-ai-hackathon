// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import ApprovalExportPanel from '../approval-export-panel';
import ContentStudioPanel from '../content-studio-panel';
import VisualIntelligencePanel from '../visual-intelligence-panel';
import VoiceBriefPanel from '../voice-brief-panel';

describe('Growth Engine Frontend Component Suite', () => {
  it('renders VoiceBriefPanel and handles brief changes', () => {
    const handleBriefChange = vi.fn();
    const sampleBrief = {
      property_id: '20000000-0000-4000-8000-000000000001',
      input_mode: 'voice' as const,
      raw_input: 'Cedar Apartment 2 BHK Indiranagar with sunny balcony',
      target_audience: 'Working couples',
      key_selling_points: ['Sunny balcony'],
      tone: 'warm' as const,
      campaign_objective: 'lead_generation' as const,
      special_offers: '',
    };

    render(
      <VoiceBriefPanel
        propertyId="20000000-0000-4000-8000-000000000001"
        brief={sampleBrief}
        onBriefChange={handleBriefChange}
        demoPresets={[{ title: 'Family Focus', transcript: 'Test', tone: 'warm', campaign_objective: 'lead_generation' }]}
      />
    );

    expect(screen.getByText(/Meta Muse Voice Transcribe/i)).toBeDefined();
    expect(screen.getByDisplayValue(/Cedar Apartment 2 BHK Indiranagar/i)).toBeDefined();
  });

  it('renders VisualIntelligencePanel with authentic photo and SAM features', () => {
    const images = [
      {
        id: 'img_1',
        property_id: 'prop_1',
        image_url: 'https://example.com/photo.jpg',
        caption: 'Balcony facade',
        is_authentic: true,
        features: ['Balcony'],
        created_at: new Date().toISOString(),
      },
    ];

    const features = [
      { feature_id: 'f1', label: 'Eastern Balcony', confidence: 0.94, description: 'Spacious balcony' },
      { feature_id: 'f2', label: 'Modular Kitchen', confidence: 0.92, description: 'Granite counters' },
    ];

    render(
      <VisualIntelligencePanel
        propertyId="prop_1"
        images={images}
        selectedImageIndex={0}
        onSelectImage={vi.fn()}
        features={features}
        onFeaturesDetected={vi.fn()}
      />
    );

    expect(screen.getByText(/Authentic Property Photography/i)).toBeDefined();
    expect(screen.getByText(/Eastern Balcony/i)).toBeDefined();
    expect(screen.getByText(/94% verified/i)).toBeDefined();
  });

  it('renders ContentStudioPanel and verifies grounding badge', () => {
    const contentItems = [
      {
        id: 'item_1',
        kit_id: 'kit_1',
        channel: 'instagram_feed' as const,
        headline: 'Modern 2 BHK in Indiranagar',
        body: 'Verified living space for contemporary lifestyles.',
        call_to_action: 'DM for visit',
        hashtags: ['#Indiranagar'],
        grounding_verified: true,
        verification_notes: 'Verified against CRM',
        status: 'draft' as const,
        version: 1,
        approval_notes: '',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ];

    render(
      <ContentStudioPanel
        contentItems={contentItems}
        onDecideContent={vi.fn()}
      />
    );

    expect(screen.getByText(/Meta Muse Spark Copy Studio/i)).toBeDefined();
    expect(screen.getByText(/✓ Grounding Verified/i)).toBeDefined();
    expect(screen.getByDisplayValue(/Modern 2 BHK in Indiranagar/i)).toBeDefined();
  });

  it('renders ApprovalExportPanel and allows approval action', () => {
    const handleApproveAll = vi.fn();
    const sampleKit = {
      id: 'kit_1',
      property_id: 'prop_1',
      grounding: {
        property_id: 'prop_1',
        title: 'Demo Cedar Apartment',
        locality: 'Indiranagar',
        price_inr: '10500000',
        bedrooms: 2,
        description: '2 BHK',
        active: true,
        grounding_version: 1,
        grounding_hash: '1234',
      },
      objective: 'lead_generation',
      status: 'draft' as const,
      version: 1,
      content_items: [],
      creative_assets: [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    render(
      <ApprovalExportPanel
        kit={sampleKit}
        onApproveAll={handleApproveAll}
      />
    );

    expect(screen.getByText(/Owner Approval & Asset Export/i)).toBeDefined();
    const approveBtn = screen.getByText(/✓ Approve Entire Marketing Kit/i);
    fireEvent.click(approveBtn);
    expect(handleApproveAll).toHaveBeenCalled();
  });
});
