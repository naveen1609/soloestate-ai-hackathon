export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function api(path: string, init?: RequestInit): Promise<unknown> {
  const response = await fetch(path, { ...init, cache: "no-store", headers: { "Content-Type": "application/json", ...init?.headers } });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const message = body && typeof body.message === "string" ? body.message : body && typeof body.detail === "string" ? body.detail : "The request could not be confirmed. Refresh the saved run before retrying.";
    throw new ApiError(message, response.status);
  }
  return body;
}
