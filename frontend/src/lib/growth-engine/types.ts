export type InputMode = 'text' | 'voice';
export type CampaignTone = 'professional' | 'luxury' | 'warm' | 'urgent' | 'festive';
export type CampaignObjective = 'lead_generation' | 'brand_awareness' | 'open_house' | 'festive_offer' | 'price_drop';
export type ChannelType = 'instagram_feed' | 'instagram_story' | 'facebook_feed' | 'festival_offer';
export type ItemStatus = 'draft' | 'approved' | 'rejected' | 'superseded';

export interface PropertyItem {
  id: string;
  title: string;
  locality: string;
  price: string;
  bedrooms: number;
  description: string;
  active: boolean;
  authentic_images_count: number;
  images: PropertyImage[];
}

export interface PropertyImage {
  id: string;
  property_id: string;
  image_url: string;
  caption: string;
  is_authentic: boolean;
  features: string[];
  created_at: string;
}

export interface SegmentedFeature {
  feature_id: string;
  label: string;
  confidence: number;
  bounding_box?: [number, number, number, number];
  segmentation_mask_svg?: string;
  description: string;
}

export interface MarketingBrief {
  property_id: string;
  input_mode: InputMode;
  raw_input: string;
  target_audience: string;
  key_selling_points: string[];
  tone: CampaignTone;
  campaign_objective: CampaignObjective;
  special_offers: string;
}

export interface CreativeAsset {
  id: string;
  kit_id: string;
  property_id: string;
  source_image_id?: string;
  asset_type: string;
  is_authentic: boolean;
  concept_watermark: string;
  image_url: string;
  segmentation_data: Record<string, any>;
  prompt_text: string;
  prompt_hash: string;
  model_id: string;
  status: ItemStatus;
  version: number;
  source?: string;
  rejection_reason?: string;
  created_at: string;
}

export interface FactReference {
  fact_key: string;
  crm_version: number;
  rendered_value: string;
  source_field?: string;
}

export interface ContentItem {
  id: string;
  kit_id: string;
  channel: ChannelType;
  headline: string;
  body: string;
  call_to_action: string;
  hashtags: string[];
  creative_asset_id?: string;
  grounding_verified: boolean;
  verification_notes: string;
  fact_references?: FactReference[];
  status: ItemStatus;
  approval_notes: string;
  approved_at?: string;
  version: number;
  source?: string;
  claim_checks?: Record<string, any>;
  rejection_reason?: string;
  created_at: string;
  updated_at: string;
}

export interface CampaignPlan {
  id: string;
  kit_id: string;
  property_id: string;
  objective: string;
  target_audience_spec: {
    geographic_targeting?: { city: string; center_locality: string; radius_km: number };
    demographics?: { age_range: [number, number]; income_band: string };
    interests?: string[];
    behavioral_signals?: string[];
  };
  recommended_placements: string[];
  suggested_daily_budget: number;
  suggested_duration_days: number;
  rationale: string;
  staged_launch_payload: Record<string, any>;
  status: ItemStatus;
  approved_at?: string;
  version: number;
  rejection_reason?: string;
  created_at: string;
  updated_at: string;
}

export interface PropertyGrounding {
  property_id: string;
  title: string;
  locality: string;
  price_inr: string;
  bedrooms: number;
  description: string;
  active: boolean;
  grounding_version: number;
  grounding_hash: string;
}

export interface KitSummary {
  id: string;
  property_id: string;
  brief_id?: string;
  grounding: PropertyGrounding;
  objective: string;
  status: 'draft' | 'partially_approved' | 'approved' | 'archived';
  version: number;
  content_items: ContentItem[];
  creative_assets: CreativeAsset[];
  campaign_plan?: CampaignPlan;
  created_at: string;
  updated_at: string;
}

export interface OrchestrationStep {
  step_name: string;
  model_id: string;
  status: 'completed' | 'skipped' | 'error';
  details: string;
  duration_ms: number;
}

export interface OrchestrateResponse {
  kit: KitSummary;
  steps_executed: OrchestrationStep[];
}

export interface ExportPackage {
  kit_id: string;
  property_id: string;
  property_title: string;
  locality: string;
  price_formatted: string;
  approved_content: Array<{
    id: string;
    channel: string;
    headline: string;
    body: string;
    call_to_action: string;
    hashtags: string[];
    status: string;
    grounding_verified: boolean;
    version: number;
  }>;
  approved_creatives: Array<{
    id: string;
    asset_type: string;
    is_authentic: boolean;
    concept_watermark: string;
    image_url: string;
    prompt_hash: string;
    status: string;
    version: number;
  }>;
  campaign_plan?: Record<string, any>;
  export_timestamp: string;
  safety_disclaimer: string;
}
