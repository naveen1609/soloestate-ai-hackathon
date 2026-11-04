import { describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { parseGrowthBody, permittedGrowthPath, POST } from './route';

describe('Growth Engine BFF route permission gate', () => {
  it('permits authorized GET endpoints', () => {
    expect(permittedGrowthPath('GET', 'properties')).toBe(true);
    expect(permittedGrowthPath('GET', 'properties/20000000-0000-4000-8000-000000000001/images')).toBe(true);
    expect(permittedGrowthPath('GET', 'kits')).toBe(true);
    expect(permittedGrowthPath('GET', 'kits/10000000-0000-4000-8000-000000000001')).toBe(true);
    expect(permittedGrowthPath('GET', 'kits/10000000-0000-4000-8000-000000000001/export')).toBe(true);
  });

  it('permits authorized POST endpoints', () => {
    expect(permittedGrowthPath('POST', 'transcribe-voice')).toBe(true);
    expect(permittedGrowthPath('POST', 'visual-segment')).toBe(true);
    expect(permittedGrowthPath('POST', 'generate-creative')).toBe(true);
    expect(permittedGrowthPath('POST', 'generate-content')).toBe(true);
    expect(permittedGrowthPath('POST', 'generate-plan')).toBe(true);
    expect(permittedGrowthPath('POST', 'orchestrate-kit')).toBe(true);
  });

  it('permits authorized PATCH decision endpoints', () => {
    const kit = '10000000-0000-4000-8000-000000000001';
    const item = '20000000-0000-4000-8000-000000000002';
    expect(permittedGrowthPath('PATCH', `kits/${kit}/content-items/${item}/decision`)).toBe(true);
    expect(permittedGrowthPath('PATCH', `kits/${kit}/creative-assets/${item}/decision`)).toBe(true);
    expect(permittedGrowthPath('PATCH', `kits/${kit}/campaign-plan/decision`)).toBe(true);
  });

  it('rejects unpermitted paths and arbitrary methods', () => {
    expect(permittedGrowthPath('GET', 'crm/leads')).toBe(false);
    expect(permittedGrowthPath('DELETE', 'kits')).toBe(false);
    expect(permittedGrowthPath('POST', 'properties')).toBe(false);
    expect(permittedGrowthPath('GET', 'kits/malicious-id/export')).toBe(false);
  });
});

describe('Growth Engine BFF body parsing and audio limits', () => {
  it('permits realistic 100KB WAV base64 audio payload through transcribe-voice', async () => {
    const audioData = 'A'.repeat(100 * 1024); // 100KB audio
    const payload = JSON.stringify({
      property_id: '20000000-0000-4000-8000-000000000001',
      audio_base64: audioData,
      audio_format: 'wav',
    });

    const req = new NextRequest('http://localhost:3000/api/growth-engine/transcribe-voice', {
      method: 'POST',
      body: payload,
    });

    const parsed = await parseGrowthBody(req, true);
    expect(parsed).toBeDefined();
    expect((parsed as any).audio_format).toBe('wav');
  });

  it('rejects audio payload exceeding 15MB limit with 413', async () => {
    const hugeAudio = 'A'.repeat(16 * 1024 * 1024); // 16MB
    const payload = JSON.stringify({
      property_id: '20000000-0000-4000-8000-000000000001',
      audio_base64: hugeAudio,
    });

    const req = new NextRequest('http://localhost:3000/api/growth-engine/transcribe-voice', {
      method: 'POST',
      body: payload,
    });

    await expect(parseGrowthBody(req, true)).rejects.toMatchObject({
      statusCode: 413,
      message: expect.stringContaining('10MB'),
    });
  });

  it('rejects non-voice payload exceeding 500KB limit with 413', async () => {
    const bigData = 'X'.repeat(600 * 1024); // 600KB
    const payload = JSON.stringify({ data: bigData });

    const req = new NextRequest('http://localhost:3000/api/growth-engine/generate-content', {
      method: 'POST',
      body: payload,
    });

    await expect(parseGrowthBody(req, false)).rejects.toMatchObject({
      statusCode: 413,
    });
  });
});


it('forwards a PCM WAV above the shared JSON limit through the owner BFF', async () => {
  const audio = Buffer.alloc(44 + 64_000);
  audio.write('RIFF', 0);
  audio.writeUInt32LE(audio.length - 8, 4);
  audio.write('WAVEfmt ', 8);
  audio.writeUInt32LE(16, 16);
  audio.writeUInt16LE(1, 20);
  audio.writeUInt16LE(1, 22);
  audio.writeUInt32LE(16_000, 24);
  audio.writeUInt32LE(32_000, 28);
  audio.writeUInt16LE(2, 32);
  audio.writeUInt16LE(16, 34);
  audio.write('data', 36);
  audio.writeUInt32LE(64_000, 40);
  const body = JSON.stringify({ audio_base64: audio.toString('base64'), audio_format: 'wav' });
  const upstream = vi.fn(async () => Response.json({ transcript: 'fixture' }));
  vi.stubGlobal('fetch', upstream);
  try {
    const request = new NextRequest('http://localhost:3000/api/growth-engine/transcribe-voice', {
      method: 'POST',
      headers: { origin: 'http://localhost:3000', host: 'localhost:3000', cookie: 'soloestate_session=sess_fixture' },
      body,
    });
    const result = await POST(request, { params: Promise.resolve({ path: ['transcribe-voice'] }) });
    expect(result.status).toBe(200);
    expect(upstream).toHaveBeenCalledWith(
      expect.stringContaining('/owner/growth-engine/transcribe-voice'),
      expect.objectContaining({ body, headers: expect.objectContaining({ Authorization: 'Bearer sess_fixture' }) }),
    );
  } finally {
    vi.unstubAllGlobals();
  }
});
