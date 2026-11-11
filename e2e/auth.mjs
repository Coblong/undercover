// A stand-in for Supabase Auth, on port 8004. It signs tokens with a key of its
// own, serves the public key, and answers the browser's cross-origin checks.
// The real Supabase is never reached by the tests.
import { createServer } from "node:http";
import { generateKeyPairSync, sign } from "node:crypto";
import { readFileSync } from "node:fs";

const PASSWORD = "e2e-password";
const handlers = JSON.parse(readFileSync("data/handlers.json", "utf8"));
const { publicKey, privateKey } = generateKeyPairSync("ec", {
  namedCurve: "P-256",
});
const kid = "e2e-key";
const jwk = {
  ...publicKey.export({ format: "jwk" }),
  kid,
  alg: "ES256",
  use: "sig",
};

const encode = (value) =>
  Buffer.from(JSON.stringify(value)).toString("base64url");

const session = (email) => {
  const issued = Math.floor(Date.now() / 1000);
  const user = {
    id: `user-${email}`,
    aud: "authenticated",
    role: "authenticated",
    email,
    email_confirmed_at: new Date().toISOString(),
    app_metadata: {},
    user_metadata: {},
    created_at: new Date().toISOString(),
  };
  const head = encode({ alg: "ES256", typ: "JWT", kid });
  const body = encode({
    sub: user.id,
    email,
    aud: "authenticated",
    role: "authenticated",
    iat: issued,
    exp: issued + 3600,
  });
  const signature = sign("sha256", Buffer.from(`${head}.${body}`), {
    key: privateKey,
    dsaEncoding: "ieee-p1363",
  }).toString("base64url");
  return {
    access_token: `${head}.${body}.${signature}`,
    token_type: "bearer",
    expires_in: 3600,
    expires_at: issued + 3600,
    refresh_token: `refresh-${email}`,
    user,
  };
};

const cors = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "*",
  "access-control-allow-methods": "GET, POST, PUT, OPTIONS",
};

const json = (response, status, body) => {
  response.writeHead(status, { "content-type": "application/json", ...cors });
  response.end(JSON.stringify(body));
};

const readBody = async (request) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString() || "{}");
};

createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  if (request.method === "OPTIONS" || url.pathname === "/auth/v1/logout") {
    response.writeHead(204, cors);
    response.end();
  } else if (url.pathname === "/health") {
    json(response, 200, { status: "ok" });
  } else if (url.pathname === "/auth/v1/.well-known/jwks.json") {
    json(response, 200, { keys: [jwk] });
  } else if (url.pathname === "/auth/v1/token") {
    const body = await readBody(request);
    if (url.searchParams.get("grant_type") === "refresh_token") {
      const email = String(body.refresh_token).replace("refresh-", "");
      json(response, 200, session(email));
    } else if (
      body.password === PASSWORD &&
      handlers.some((handler) => handler.email === body.email)
    ) {
      json(response, 200, session(body.email));
    } else {
      json(response, 400, {
        error_code: "invalid_credentials",
        msg: "Invalid login credentials",
      });
    }
  } else {
    json(response, 404, { msg: "Not found" });
  }
}).listen(8004, "127.0.0.1");
