export async function GET() {
  try {
    const base = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
    const response = await fetch(`${base.replace(/\/$/, "")}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    const body: unknown = await response.json();
    if (!response.ok || typeof body !== "object" || body === null ||
        !("status" in body) || body.status !== "ok" ||
        !("service" in body) || body.service !== "soloestate-backend") {
      throw new Error("Invalid backend health response");
    }
    return Response.json({ status: "ok", backend: body });
  } catch {
    return Response.json({ status: "unavailable", message: "Backend health check failed." }, { status: 503 });
  }
}
