import { api } from '../api';
import {
  CampaignPlan,
  ContentItem,
  CreativeAsset,
  ExportPackage,
  ItemStatus,
  KitSummary,
  MarketingBrief,
  OrchestrateResponse,
  PropertyItem,
  SegmentedFeature,
} from './types';

export async function fetchGrowthProperties(): Promise<{ properties: PropertyItem[]; demo_voice_briefs: any[] }> {
  return (await api('/api/growth-engine/properties')) as { properties: PropertyItem[]; demo_voice_briefs: any[] };
}

export async function transcribeVoice(payload: {
  property_id: string;
  audio_base64?: string;
  audio_format?: string;
  raw_text?: string;
}): Promise<{ transcript: string; model_id: string; confidence: number }> {
  return (await api('/api/growth-engine/transcribe-voice', {
    method: 'POST',
    body: JSON.stringify(payload),
  })) as { transcript: string; model_id: string; confidence: number };
}

export async function segmentVisualFeatures(payload: {
  property_id: string;
  image_id: string;
}): Promise<{ features: SegmentedFeature[]; architectural_summary: string; is_authentic: boolean }> {
  return (await api('/api/growth-engine/visual-segment', {
    method: 'POST',
    body: JSON.stringify(payload),
  })) as { features: SegmentedFeature[]; architectural_summary: string; is_authentic: boolean };
}

export async function orchestrateKit(payload: {
  property_id: string;
  brief: MarketingBrief;
  selected_image_ids?: string[];
  channels?: string[];
  generate_creative?: boolean;
  generate_plan?: boolean;
}): Promise<OrchestrateResponse> {
  return (await api('/api/growth-engine/orchestrate-kit', {
    method: 'POST',
    body: JSON.stringify(payload),
  })) as OrchestrateResponse;
}

export async function fetchKitDetail(kitId: string): Promise<KitSummary> {
  return (await api(`/api/growth-engine/kits/${kitId}`)) as KitSummary;
}

export async function decideContentItem(
  kitId: string,
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
): Promise<ContentItem> {
  return (await api(`/api/growth-engine/kits/${kitId}/content-items/${itemId}/decision`, {
    method: 'PATCH',
    body: JSON.stringify(decision),
  })) as ContentItem;
}

export async function decideCreativeAsset(
  kitId: string,
  assetId: string,
  decision: { status: ItemStatus; expected_version?: number; notes?: string }
): Promise<CreativeAsset> {
  return (await api(`/api/growth-engine/kits/${kitId}/creative-assets/${assetId}/decision`, {
    method: 'PATCH',
    body: JSON.stringify(decision),
  })) as CreativeAsset;
}

export async function decideCampaignPlan(
  kitId: string,
  decision: { plan_id: string; status: ItemStatus; expected_version?: number; notes?: string }
): Promise<CampaignPlan> {
  return (await api(`/api/growth-engine/kits/${kitId}/campaign-plan/decision`, {
    method: 'PATCH',
    body: JSON.stringify(decision),
  })) as CampaignPlan;
}

export async function exportKit(kitId: string): Promise<ExportPackage> {
  return (await api(`/api/growth-engine/kits/${kitId}/export`)) as ExportPackage;
}
