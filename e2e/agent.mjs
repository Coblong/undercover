// A stand-in for the agent's service, on port 8003. It records the calls the
// backend makes and pays the claim through the backend, as the real service
// would after a sign-off. The real service is never started by the tests.
import { createServer } from "node:http";

const backend = process.env.BACKEND_URL;
const delay = Number(process.env.PAY_DELAY_MS ?? 1500);
const received = [];

const json = (response, status, body) => {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
};

const readBody = async (request) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString() || "{}");
};

const pay = async (claimId) => {
  const claim = await (await fetch(`${backend}/api/claims/${claimId}`)).json();
  await fetch(`${backend}/api/claims/${claimId}/payment`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ amount: claim.decision.payout }),
  });
};

createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  const work = /^\/claims\/([^/]+)\/work$/.exec(url.pathname);
  if (request.method === "GET" && url.pathname === "/health") {
    json(response, 200, { status: "ok" });
  } else if (request.method === "GET" && url.pathname === "/received") {
    json(response, 200, received);
  } else if (request.method === "POST" && work) {
    const { thread_id: threadId } = await readBody(request);
    received.push({ claim_id: work[1], thread_id: threadId });
    await new Promise((resolve) => setTimeout(resolve, delay));
    await pay(work[1]);
    json(response, 200, { reply: "paid" });
  } else {
    json(response, 404, { detail: "Not found" });
  }
}).listen(8003, "127.0.0.1");
