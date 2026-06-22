# Live Job Map — How We Roll It Out to the Team

*Prepared for review — one decision needed (see the end).*

## What it is

A live map of all our scheduled jobs, pulled automatically from our Teamup
calendar. Every job that has an address shows up as a pin, and it refreshes on
its own as the calendar changes — no one has to update it by hand. It's the
"see every job at once" view that Teamup doesn't give us: handy for dispatching,
routing crews efficiently, and seeing where the day's work clusters.

**It's already working against our real calendar** — about 100 of our upcoming
jobs are plotted right now. It shows the same job info that's already in Teamup
(customer, time, address), just laid out on a map.

## Three things that are true no matter which option we pick

- **It's read-only.** The map can only *view* the calendar. It can never add,
  change, move, or delete anything in Teamup. The worst it can ever do is show a
  slightly out-of-date pin until its next refresh.
- **No new software cost.** It uses free mapping and address-lookup services.
  No subscription, no per-job fee.
- **Private by invitation.** Only people we hand the link to can see it. It's not
  posted publicly or searchable.

## The decision: how should the team get to it?

### Option A — Each person runs it on their own computer
Everyone who needs it installs a copy on their PC and opens it there.
- 👍 No shared equipment needed.
- 👎 A one-time setup on each person's computer; not friendly for phones/tablets.
- **Best if:** only one or two office people ever need it.

### Option B — One shared screen in the office *(simplest good answer)*
One always-on computer in the office runs it, and anyone on the office network
opens it in their browser like an internal website.
- 👍 Set up once; everyone sees the same map; nothing to install on other machines.
- 👎 Only works inside the office (not crews out in the field) — unless we also do C.
- **Needs:** one computer that stays on. An old PC or an inexpensive mini-PC is plenty.

### Option C — Reachable from anywhere (office + crews in their trucks)
The same shared map as Option B, but also reachable from phones and tablets out
in the field, protected by a shared password.
- 👍 Dispatcher *and* crew leads can pull it up anywhere; works well on a phone.
- 👎 A little more setup; we put a password on it so it stays private.
- **Cost:** still effectively free (a free secure connection), or a few dollars a
  month if we later want something more polished.

## My recommendation

- If the main user is whoever schedules/dispatches from the office → **start with B.**
- If crew leads need it in the trucks → go straight to **C.**

We can always begin with B and add C later — it's the same map either way, so this
isn't a one-way door.

## What I need from you to set it up

1. **Who needs to see the map?** Just the office/dispatcher, or crews in the field too?
2. **On what devices?** Office computers only, or phones/tablets as well?
3. **If field access (C):** are you OK with it being reachable over the internet
   behind a shared password?

Answer those three and I'll get it running for the team.

---

*One note on access, in plain terms:* sharing is no problem — the same view-link
and connection can serve the whole team. With the shared options (B and C), the
sensitive connection detail lives on just one computer, which is the safest setup.
Happy to walk through the technical specifics if useful.
