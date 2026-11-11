// A stand-in for OpenRouter's Decisions endpoint, on port 8005. It grades every
// explanation the same way. The real judge is never called by the tests.
import { createServer } from "node:http";

const answer = {
  answers: {
    grade: {
      type: "score",
      score: 2.4,
      probabilities: { 0: 0, 1: 0.1, 2: 0.5, 3: 0.4 },
    },
    same_grounds: { type: "noul", noul: 0.9 },
    same_section: { type: "noul", noul: 0.1 },
  },
  model: "typesafe/jev-1.13",
  usage: { input_tokens: 10, output_tokens: 2, cost: 0.0002 },
};

const json = (response, status, body) => {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
};

createServer((request, response) => {
  if (request.method === "GET" && request.url === "/health") {
    json(response, 200, { status: "ok" });
  } else if (
    request.method === "POST" &&
    request.url === "/api/alpha/decisions"
  ) {
    request.resume();
    request.on("end", () => {
      json(response, 200, answer);
    });
  } else {
    json(response, 404, { detail: "Not found" });
  }
}).listen(8005, "127.0.0.1");
