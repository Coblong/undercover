// A stand-in for the agent's service, on port 8003. It records the calls the
// backend makes and pays the claim through the backend, as the real service
// would after a sign-off. The real service is never started by the tests.
import { createServer } from "node:http";

const backend = process.env.BACKEND_URL;
const delay = Number(process.env.PAY_DELAY_MS ?? 1500);
const received = [];
const started = [];
const rounds = { started: [], decided: [] };

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

// Create a run through the backend as the real service does, and pass a refusal on.
const startRun = async (path, body, response) => {
  const created = await fetch(`${backend}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const run = await created.json();
  if (!created.ok) return json(response, created.status, run);
  json(response, 202, { run_id: run.run_id });
};

const post = (path, body) =>
  fetch(`${backend}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

const startDispatch = async (request, response) => {
  const { concurrency } = await readBody(request);
  started.push({ kind: "dispatch", concurrency });
  await startRun("/api/runs/dispatch", { concurrency }, response);
};

const startEvaluation = async (request, response) => {
  const { concurrency, eval_set: evalSet, tiny } = await readBody(request);
  started.push({ kind: "evaluation", concurrency, eval_set: evalSet, tiny });
  const body = { concurrency, eval_set: evalSet, tiny, round_id: null };
  await startRun("/api/runs/evaluation", body, response);
};

// A round starts and a decision is recorded through the backend, as the service does.
const startRound = async (request, response) => {
  const { handler_id: handlerId, handler_name: handlerName } =
    await readBody(request);
  rounds.started.push({ handler_id: handlerId, handler_name: handlerName });
  const created = await post("/api/improvement/rounds", {
    handler_id: handlerId,
  });
  const round = await created.json();
  if (!created.ok) return json(response, created.status, round);
  json(response, 202, { run_id: round.id });
};

const decideRound = async (request, response, roundId) => {
  const body = await readBody(request);
  rounds.decided.push({ round_id: Number(roundId), ...body });
  const recorded = await post(
    `/api/improvement/rounds/${roundId}/decision`,
    body,
  );
  const answer = await recorded.json();
  if (!recorded.ok) return json(response, recorded.status, answer);
  json(response, 202, { run_id: Number(roundId) });
};

const workClaim = async (request, response, claimId) => {
  const { thread_id: threadId } = await readBody(request);
  received.push({ claim_id: claimId, thread_id: threadId });
  await new Promise((resolve) => setTimeout(resolve, delay));
  await pay(claimId);
  json(response, 200, { reply: "paid" });
};

const routes = [
  ["GET", /^\/health$/, (_, response) => json(response, 200, { status: "ok" })],
  ["GET", /^\/received$/, (_, response) => json(response, 200, received)],
  ["GET", /^\/started$/, (_, response) => json(response, 200, started)],
  ["GET", /^\/rounds-received$/, (_, response) => json(response, 200, rounds)],
  ["POST", /^\/dispatches$/, startDispatch],
  ["POST", /^\/evaluations$/, startEvaluation],
  ["POST", /^\/rounds$/, startRound],
  ["POST", /^\/rounds\/(\d+)\/decision$/, decideRound],
  ["POST", /^\/claims\/([^/]+)\/work$/, workClaim],
];

createServer(async (request, response) => {
  const url = new URL(request.url, "http://localhost");
  for (const [method, pattern, handle] of routes) {
    const match = pattern.exec(url.pathname);
    if (request.method === method && match) {
      return handle(request, response, match[1]);
    }
  }
  json(response, 404, { detail: "Not found" });
}).listen(8003, "127.0.0.1");
