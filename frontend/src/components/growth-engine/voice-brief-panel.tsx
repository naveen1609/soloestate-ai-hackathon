'use client';
import { useRef, useState } from 'react';
import { transcribeVoice } from '../../lib/growth-engine/api';
import { CampaignObjective, CampaignTone, MarketingBrief } from '../../lib/growth-engine/types';

interface VoiceBriefPanelProps {
  propertyId: string;
  brief: MarketingBrief;
  onBriefChange: (brief: MarketingBrief) => void;
  demoPresets?: Array<{ title: string; transcript: string; tone: string; campaign_objective: string }>;
}

function writeString(view: DataView, offset: number, str: string) {
  for (let i = 0; i < str.length; i++) {
    view.setUint8(offset + i, str.charCodeAt(i));
  }
}

function encodeWAV(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);

  // RIFF header
  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(view, 8, 'WAVE');

  // fmt subchunk (16-bit mono PCM)
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM format
  view.setUint16(22, 1, true); // Mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // Byte rate
  view.setUint16(32, 2, true); // Block align
  view.setUint16(34, 16, true); // Bits per sample

  // data subchunk
  writeString(view, 36, 'data');
  view.setUint32(40, samples.length * 2, true);

  // Write PCM samples
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }

  return new Blob([buffer], { type: 'audio/wav' });
}

export default function VoiceBriefPanel({
  propertyId,
  brief,
  onBriefChange,
  demoPresets = [],
}: VoiceBriefPanelProps) {
  const [isRecording, setIsRecording] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const scriptProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const audioChunksRef = useRef<Float32Array[]>([]);

  const handleApplyTextPreset = (preset: { title: string; transcript: string; tone: string; campaign_objective: string }) => {
    setError('');
    onBriefChange({
      ...brief,
      input_mode: 'text',
      raw_input: preset.transcript,
      tone: preset.tone as CampaignTone,
      campaign_objective: preset.campaign_objective as CampaignObjective,
    });
  };

  const startRecording = async () => {
    setError('');
    if (!navigator?.mediaDevices?.getUserMedia) {
      setError('Microphone access is not supported by your browser or environment. Please enter text or use a preset.');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaStreamRef.current = stream;

      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      const audioCtx = new AudioCtx({ sampleRate: 16000 });
      audioContextRef.current = audioCtx;

      const source = audioCtx.createMediaStreamSource(stream);
      const processor = audioCtx.createScriptProcessor(4096, 1, 1);
      scriptProcessorRef.current = processor;
      audioChunksRef.current = [];

      processor.onaudioprocess = (e) => {
        const inputData = e.inputBuffer.getChannelData(0);
        audioChunksRef.current.push(new Float32Array(inputData));
      };

      source.connect(processor);
      processor.connect(audioCtx.destination);
      setIsRecording(true);
    } catch (err: any) {
      setError(`Microphone permission denied or unavailable: ${err?.message || 'Unknown error'}`);
      setIsRecording(false);
    }
  };

  const stopRecording = async () => {
    setIsRecording(false);

    if (scriptProcessorRef.current) {
      scriptProcessorRef.current.disconnect();
      scriptProcessorRef.current = null;
    }

    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }

    const audioCtx = audioContextRef.current;
    const sampleRate = audioCtx?.sampleRate || 16000;
    if (audioCtx && audioCtx.state !== 'closed') {
      await audioCtx.close();
      audioContextRef.current = null;
    }

    const chunks = audioChunksRef.current;
    if (!chunks.length) {
      setError('No audio captured.');
      return;
    }

    let totalLength = 0;
    for (const c of chunks) totalLength += c.length;
    const flatSamples = new Float32Array(totalLength);
    let offset = 0;
    for (const c of chunks) {
      flatSamples.set(c, offset);
      offset += c.length;
    }

    const wavBlob = encodeWAV(flatSamples, sampleRate);
    if (wavBlob.size < 100) {
      setError('Recorded audio was too brief. Please speak clearly and try again.');
      return;
    }
    if (wavBlob.size > 10 * 1024 * 1024) {
      setError('Audio recording exceeds 10MB limit. Please keep voice notes concise.');
      return;
    }

    setLoading(true);
    try {
      const reader = new FileReader();
      reader.readAsDataURL(wavBlob);
      reader.onloadend = async () => {
        const base64Audio = (reader.result as string).split(',')[1];
        try {
          const res = await transcribeVoice({
            property_id: propertyId,
            audio_base64: base64Audio,
            audio_format: 'wav',
          });
          onBriefChange({
            ...brief,
            input_mode: 'voice',
            raw_input: res.transcript,
          });
        } catch (err: any) {
          setError(err?.message || 'Meta Muse Voice Transcribe failed to process the recorded audio.');
        } finally {
          setLoading(false);
        }
      };
    } catch {
      setError('Failed to process recorded audio.');
      setLoading(false);
    }
  };

  const handleToggleRecord = () => {
    if (isRecording) {
      void stopRecording();
    } else {
      void startRecording();
    }
  };

  return (
    <div className="ge-grid-2">
      <div className="ge-card">
        <div className="ge-card-header">
          <h2>🎙️ Meta Muse Voice Transcribe</h2>
          <span className="ge-status-pill draft">Voice Engine</span>
        </div>
        <p style={{ fontSize: '13px', color: '#64748b', marginBottom: '16px' }}>
          Record your marketing notes live or enter text. Meta Muse Voice converts spoken audio into an actionable structured brief.
        </p>

        <div className="ge-voice-box">
          <button
            className={`ge-mic-btn ${isRecording ? 'recording' : ''}`}
            onClick={handleToggleRecord}
            disabled={loading}
            title={isRecording ? 'Click to Stop Recording' : 'Click to Speak'}
            type="button"
          >
            {isRecording ? '⏹' : '🎤'}
          </button>
          <strong>
            {isRecording
              ? 'Recording live microphone audio (WAV 16-bit PCM)... Click Stop when finished.'
              : loading
              ? 'Transcribing audio via Meta Muse Voice Transcribe (/v1/asr/transcribe)...'
              : 'Click mic to record spoken brief'}
          </strong>
          <span style={{ fontSize: '12px', color: '#64748b' }}>
            Supported input: 16-bit PCM WAV (direct microphone capture)
          </span>
        </div>

        {error && <p className="notice notice-error">{error}</p>}

        {demoPresets.length > 0 && (
          <div style={{ marginTop: '20px' }}>
            <span style={{ fontSize: '12px', fontWeight: 600, color: '#475569' }}>
              Quick Preset Transcripts (Text Mode):
            </span>
            <div className="ge-preset-chips">
              {demoPresets.map((preset, idx) => (
                <button
                  key={idx}
                  type="button"
                  className="ge-chip"
                  onClick={() => handleApplyTextPreset(preset)}
                >
                  {preset.title}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Structured Brief Form */}
      <div className="ge-card">
        <div className="ge-card-header">
          <h2>📋 Structured Marketing Brief</h2>
          <span className="ge-status-pill approved">Structured</span>
        </div>

        <div className="ge-form-group">
          <label>Raw Marketing Notes / Voice Transcript:</label>
          <textarea
            rows={4}
            value={brief.raw_input}
            onChange={(e) => onBriefChange({ ...brief, raw_input: e.target.value })}
            placeholder="Describe property features, balcony views, lighting, kitchen layout, and target buyer profile..."
          />
        </div>

        <div className="ge-form-group">
          <label>Target Audience:</label>
          <input
            type="text"
            value={brief.target_audience}
            onChange={(e) => onBriefChange({ ...brief, target_audience: e.target.value })}
          />
        </div>

        <div className="ge-grid-2" style={{ gap: '12px' }}>
          <div className="ge-form-group">
            <label>Campaign Tone:</label>
            <select
              value={brief.tone}
              onChange={(e) => onBriefChange({ ...brief, tone: e.target.value as CampaignTone })}
            >
              <option value="warm">Warm & Welcoming</option>
              <option value="luxury">Luxury & Exclusive</option>
              <option value="professional">Professional & Direct</option>
              <option value="urgent">Urgent & Fast-Moving</option>
              <option value="festive">Festive Celebration</option>
            </select>
          </div>

          <div className="ge-form-group">
            <label>Campaign Objective:</label>
            <select
              value={brief.campaign_objective}
              onChange={(e) => onBriefChange({ ...brief, campaign_objective: e.target.value as CampaignObjective })}
            >
              <option value="lead_generation">Lead Generation & Visits</option>
              <option value="brand_awareness">Brand Awareness</option>
              <option value="open_house">Open House RSVP</option>
              <option value="festive_offer">Festive Offer</option>
              <option value="price_drop">Price Adjustment</option>
            </select>
          </div>
        </div>

        <div className="ge-form-group">
          <label>Optional Incentive / Offer Notes:</label>
          <input
            type="text"
            value={brief.special_offers}
            onChange={(e) => onBriefChange({ ...brief, special_offers: e.target.value })}
            placeholder="e.g. Complimentary private walkthrough slot, flexible possession"
          />
        </div>
      </div>
    </div>
  );
}
