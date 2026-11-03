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

The agent also runs the queue. One run works every waiting claim, choosing the order itself, and ends with a short summary for the handlers. A run can be capped at a number of claims, with the rest left waiting, and the agent can still be run on one named claim. It plans its work as a to-do list and hands each claim to a fresh helper that works that claim alone and reports back in a line.

When the agent approves a payout over $1,000 it stops and waits for a handler, keeping everything it has worked out. As soon as a handler signs off on the dashboard, the agent picks the claim up from where it stopped and pays it. Between the sign-off and the payment the claim shows as in progress, and the payment records who signed off and when. If the agent is not running at that moment, it picks the claim up the next time it runs. A handler can only sign off for now.

If the agent is stopped partway through a claim, by a crash, a restart or Ctrl-C, it carries on with that claim from where it got to the next time it runs, and that does not count as a new attempt. A queue run that was stopped is finished by the next run. A claim that fails with an error still goes back in the queue and starts afresh.

Our goal is to handle claims straight through, as accurately as our handlers do.

## What the team needs to see

- The queue, with where each claim stands: waiting, in progress, waiting for sign-off or decided.
- Each claim in full, with the agent's decision and its reasons.
- What the agent is doing on a claim while it works, step by step, and the sections of the wording and the manual it read.
- The to-do list of the current run, with each item to do, in progress, done or left for a handler, and the run's summary when it ends.
- The agent's instructions, for running the queue and for working a claim, read only.

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