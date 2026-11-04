# The claims pilot

From the head of claims operations at UnderCover Travel Insurance.

## How claims work today

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims by hand. For each claim, a handler reads the customer's story and documents and checks them against the policy and our policy wording. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

## The pilot

We want an AI agent, the UnderCover Agent, to work the queue the way a handler does. For each claim it should:

- read the claim, the customer's policy and claim history
- find and read the sections of the policy wording and the claims manual that the claim needs, as a handler would
- approve, decline or refer the claim
- work out the payout when it approves
- explain the decision in two or three sentences, along with the relevant section of the policy

Our claims arrive in batches, so a dispatch works the queue with many claim agents at once. A dispatch works every claim left in progress and every waiting claim, oldest submitted first, with up to N claim agents working at the same time. Code chooses the claims and keeps track of the run, with no model, and each claim is worked by the claim agent alone. A handler starts a dispatch on the dashboard and chooses N, from 1 to 25. Only one dispatch or evaluation runs at a time, and the run's summary comes from our records, not from a model. The agent can still be run on one named claim.

When the agent approves a payout over $1,000 it stops and waits for a handler, keeping everything it has worked out. As soon as a handler signs off on the dashboard, the agent picks the claim up from where it stopped and pays it. Between the sign-off and the payment the claim shows as in progress, and the payment records who signed off and when. If the agent is not running at that moment, it picks the claim up the next time it runs. A handler can only sign off for now.

If the agent is stopped partway through a claim, by a crash, a restart or Ctrl-C, it carries on with that claim from where it got to the next time it runs, and that does not count as a new attempt. A dispatch that was stopped is finished by the next one. A claim that hits a rate limit or a timeout waits and carries on from where it got to, as the same attempt, and one that is still hitting them stays in progress for the next dispatch. A claim that fails with any other error goes back in the queue and the same dispatch tries it once more.

Our goal is to handle claims straight through, as accurately as our handlers do.

## What the team needs to see

- The queue, with where each claim stands: waiting, in progress, waiting for sign-off or decided.
- Each claim in full, with the agent's decision and its reasons.
- What the agent is doing on a claim while it works, step by step, and the sections of the wording and the manual it read.
- The current dispatch, with each of its claims and where it stands, its model cost and time, and a summary when it ends: how many claims were approved, declined and referred, each payout waiting for a sign-off, and each claim left for a handler or still in progress.
- What each claim cost in tokens and model cost, and the time the agent spent working on it, which leaves out any wait for a handler's sign-off.
- How well the agent is doing: on the Scoreboard, an evaluation works copies of our labelled claims, and shows how often the agent agrees with our handlers, what its mistakes cost, how many claims it handles straight through, and its cost and time per claim.
- The agent's instructions for working a claim, read only.

## Rules

- A payout over $1,000 needs a handler's sign-off. The agent can approve such a claim, but the payment waits until a handler signs off. The system must block the payment until then, even if the agent tries to pay.
- Decisions follow the policy wording and the claims manual. The wording comes first.
- Problems are put right automatically. One bad claim never stops a run or leaves a claim stuck in progress. A claim that fails before it is decided goes back in the queue to be tried again, and a claim that fails twice is left for a handler. No claim is ever decided or paid twice.
- Our handlers' own decisions on these claims are in `data/evals/`. We use them to check the agent's work. The agent must never see them.
- Amounts are in US dollars.

## Our records

Everything is in `data/`, described in `data/README.md`: the claims queue, the policies, the customers, the plans, the handlers, the policy wording and the claims manual (`claims_manual.md`). The held-out claims in `data/evals/` are for checking the agent later and stay out of the queue.

## Not for now

- No sign-in. Everyone who uses the screens sees everything.
- No real payments.
- No messages to customers.