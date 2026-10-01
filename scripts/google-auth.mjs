import { createSign } from "node:crypto";
import { readFile } from "node:fs/promises";

function encoded(value) {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

export async function googleAccessToken(env = process.env, fetcher = fetch) {
  if (env.RAMP_UP_GOOGLE_ACCESS_TOKEN) return env.RAMP_UP_GOOGLE_ACCESS_TOKEN;
  if (!env.GOOGLE_SERVICE_ACCOUNT_FILE) return null;
  const credentials = JSON.parse(await readFile(env.GOOGLE_SERVICE_ACCOUNT_FILE, "utf8"));
  if (credentials.type !== "service_account" || !credentials.client_email || !credentials.private_key) {
    throw new Error("GOOGLE_SERVICE_ACCOUNT_FILE must contain service account credentials");
  }
  const now = Math.floor(Date.now() / 1000);
  const header = encoded({ alg: "RS256", typ: "JWT" });
  const payload = encoded({
    iss: credentials.client_email,
    scope: "https://www.googleapis.com/auth/drive.readonly",
    aud: "https://oauth2.googleapis.com/token",
    iat: now,
    exp: now + 3600,
  });
  const signer = createSign("RSA-SHA256");
  signer.update(`${header}.${payload}`);
  const assertion = `${header}.${payload}.${signer.sign(credentials.private_key).toString("base64url")}`;
  const response = await fetcher("https://oauth2.googleapis.com/token", {
    method: "POST",
    signal: AbortSignal.timeout(30_000),
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer", assertion }),
  });
  if (!response.ok) throw new Error(`Google OAuth returned HTTP ${response.status}`);
  const body = await response.json();
  if (!body.access_token) throw new Error("Google OAuth returned no access token");
  return body.access_token;
}
