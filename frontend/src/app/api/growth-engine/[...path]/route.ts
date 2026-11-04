import { NextRequest, NextResponse } from "next/server";
import { baseUrl, clear, COOKIE, error, sameOrigin } from "../../../../lib/server";

const uuid = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

export function permittedGrowthPath(method: string, path: string): boolean {
  if (method === "GET") {
    return new RegExp(
      `^(properties|kits)$|^properties/${uuid}/images$|^kits/${uuid}$|^kits/${uuid}/export$`
    ).test(path);
  }
  if (method === "POST") {
    return new RegExp(
      `^(transcribe-voice|visual-segment|generate-creative|generate-content|generate-plan|orchestrate-kit)$`
    ).test(path);
  }
  if (method === "PATCH") {
    return new RegExp(
      `^kits/${uuid}/(content-items|creative-assets)/${uuid}/decision$|^kits/${uuid}/campaign-plan/decision$`
    ).test(path);
  }
  return false;
}

export async function parseGrowthBody(request: NextRequest, isVoice: boolean) {
  const maxBytes = isVoice ? 15 * 1024 * 1024 : 500_000;
  const tooLarge = () => Object.assign(new Error(
    isVoice ? "Audio upload exceeds maximum payload size of 10MB." : "Request payload exceeds size limit."
  ), { statusCode: 413 });
  if (Number(request.headers.get("content-length")) > maxBytes) throw tooLarge();
  const reader = request.body?.getReader();
  if (!reader) throw new Error("Expected request body");
  let bytes = 0;
  let raw = "";
  const decoder = new TextDecoder("utf-8", { fatal: true });
  try {
    for (;;) {
      const chunk = await reader.read();
      if (chunk.done) break;
      bytes += chunk.value.byteLength;
      if (bytes > maxBytes) {
        await reader.cancel().catch(() => {});
        throw tooLarge();
      }
      raw += decoder.decode(chunk.value, { stream: true });
    }
    raw += decoder.decode();
  } finally {
    reader.releaseLock();
  }
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Expected object");
  return value;
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const session = request.cookies.get(COOKIE)?.value;
  if (!session || !session.startsWith("sess_")) {
    return error("Sign in to the owner workspace.", 401);
  }
  if (request.method !== "GET" && !sameOrigin(request)) {
    return error("Same-origin action is required.", 403);
  }

  const path = (await context.params).path.join("/");
  if (!permittedGrowthPath(request.method, path)) {
    return error("Endpoint is not permitted.", 404);
  }

  let payload: string | undefined = undefined;
  if (request.method !== "GET") {
    const isVoice = path === "transcribe-voice";
    try {
      const parsed = await parseGrowthBody(request, isVoice);
      payload = JSON.stringify(parsed);
    } catch (err: any) {
      if (err?.statusCode === 413) {
        return error(err.message, 413);
      }
      return error("Invalid request body.", 400);
    }
  }

  const query = request.nextUrl.searchParams;
  try {
    const upstreamUrl = `${baseUrl()}/owner/growth-engine/${path}${query.size ? `?${query}` : ""}`;
    const upstream = await fetch(upstreamUrl, {
      method: request.method,
      headers: {
        Authorization: `Bearer ${session}`,
        "Content-Type": "application/json",
      },
      body: payload,
      cache: "no-store",
      signal: AbortSignal.timeout(60_000),
    });

    const value: unknown = await upstream.json();
    const response = NextResponse.json(value, {
      status: upstream.status,
      headers: { "Cache-Control": "no-store" },
    });
    return upstream.status === 401 ? clear(response) : response;
  } catch {
    return error("Growth Engine backend service unavailable.", 503);
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
