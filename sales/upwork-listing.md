# Upwork listing

## SEO title (leads with the tool name)

**OpsWatch: self-hosted automation and AI monitoring deployed on your VPS, with LLM cost tracking, drift and eval alerts (n8n, Make, cron, APIs, Claude, OpenAI)**

Alternate, shorter:

**OpsWatch: deploy your automations and AI features on your own VPS with monitoring, LLM cost control, and 24/7 alerts**

## Project Catalog title (Upwork requires the "You will get" prefix, keep it short)

**You will get a self-hosted automation monitoring stack on your VPS**

## Search tags (5)

`automation` · `self hosted` · `vps` · `monitoring` · `n8n`

Other keywords to seed into the overview and skills: workflow automation, Make.com,
Zapier alternative, API integration, webhook, cron, DevOps, uptime monitoring,
SLA reporting, heartbeat monitoring, incident alerting, status dashboard,
alerting, server setup, LLM observability, LLM cost tracking, token cost,
prompt drift, eval harness, hallucination monitoring, AI quality monitoring,
Claude, OpenAI, LLMOps.

---

## Overview

Your automations only help you while they are actually running. The trouble is
that most of them fail quietly. A sync stops firing, a script errors overnight,
an API starts returning nothing, and you find out when a customer or your boss
does instead of from an alert.

I fix that by deploying a small operations stack on your own server. It runs
your jobs on a schedule, watches that they keep working, and messages you the
second something breaks. You own the server and the code, so there is no
per-task billing and no tool that can hike its price or remove a feature you
depend on.

If you ship AI features, the same stack now watches those too. It tracks what
every model call and prediction costs in real dollars, shows you the cost per
thousand predictions and what that projects to monthly at your volume, splits
spend across your cheap, standard, and hard model tiers, flags a prompt whose
output or quality shifts after you change it, and runs your labeled test sets to
catch a drop in accuracy or a rise in hallucinations before your users do. It
works with Claude or any OpenAI-compatible API, and your keys never leave your
server.

Here is what I install and configure for you:

- A scheduler that runs your jobs on an interval or at a set time each day, with
  retries and a full history of every run.
- A full set of monitors that check the things that actually break: endpoint
  uptime with response time and body checks, disk, memory, CPU and load
  thresholds, error patterns appearing in your logs, a dead-man's switch that
  notices when a scheduled job goes silent, and a webhook your own automations
  report into so a failed run shows up the instant it happens.
- Alerts to the channels you already use, Slack, Telegram, email, or any
  webhook, sent the moment something changes and again when it clears, so you
  get one alert per incident instead of a flood.
- A polished status dashboard with a live status board, per-monitor history, an
  incident timeline, and uptime and SLA figures across 24 hour, 7 day, and 30
  day windows, so you can answer "is everything running, and how has it been" at
  a glance.
- LLM observability for any AI feature you run: dollar cost per call and per
  thousand predictions, projected monthly spend at scale, spend broken down by
  model, by route, and by cheap or standard or hard tier, prompt and version
  drift detection, and a labeled eval harness that surfaces accuracy and
  hallucination as a pass or fail with a trend. Cost spikes, drift, and failing
  evals raise the same alerts as everything else.
- Everything themed to your brand: your name, logo, and colors on the dashboard,
  with an optional login in front of it.

It runs on standard Python with no external dependencies, so it installs on a
plain Linux VPS and keeps running on its own. I harden the server, set up a
service that restarts on failure and starts on boot, and put TLS and a login in
front of the dashboard.

Before you commit, I will show you the whole thing running live: scheduled jobs
firing, a monitored service going down, a pipeline reporting a failure, and the
alerts arriving within seconds, then clearing by themselves when each one
recovers, with every incident logged and counted against uptime. You watch
failures get caught in real time.

## Packages

**Deploy ($2,400, about 5 business days)**
I deploy the stack on your VPS with up to 5 jobs and 3 monitors, wire 2
notification channels, and hand it off with a runbook and a walkthrough. You run
it from there. Includes a 14 day window where I fix anything in scope at no
charge.

**Deploy + Managed ($4,800 setup, then $750/mo, most popular)**
Everything in Deploy with up to 10 jobs and 6 monitors, plus I keep watching it.
I respond to alerts by the next business day, make up to 3 changes a month, apply
security updates, and send you a monthly health report. Your first month of
monitoring is free, three month minimum after that.

**Fully Managed ($9,500 setup, then $1,800/mo)**
Everything above with unlimited jobs and monitors built to fit, custom job and
integration work, same business day priority response, up to 10 changes a month,
and a quarterly review. White-label option if you want to resell it to your own
clients.

## What I need from you

- Access to a VPS (I can recommend one that runs for about the price of a couple
  of coffees a month).
- A short written brief on what needs to run and where it breaks today.
- The chat channel you want your alerts in.

## How it works

1. You send me what needs to run and where it breaks, I confirm scope, then send
   a fixed quote. No hourly meter.
2. I deploy, wire your jobs and monitors and alerts, and test every alert by
   triggering it on purpose.
3. I hand off admin access, a runbook, and a walkthrough. You own all of it.
4. If you take a managed package, I keep it healthy so you do not have to think
   about it.

## My guarantee

Your stack is deployed and working within 5 business days of getting access, or
you do not pay the final milestone. I bill 50% to start and 50% once it is
verified running on your server.

## FAQ

**Will self-hosting become my problem to maintain?**
Not on a managed package. I run it and watch it. You get the ownership without
the babysitting.

**What if I want to stop working with you?**
You own the server and the code, and the runbook documents everything. Nothing
is locked to me. You can keep running it or hand it to anyone.

**Why not just use Zapier or Make?**
Those bill per task and hold your logic on their platform. One busy month and the
bill jumps, one deprecation and a workflow breaks. This runs on your box at a
flat cost and watches itself.

**Can you really cover uptime as one person?**
The monitoring does the watching and alerts me automatically. I am clear about
response windows up front and document everything, so nothing relies on me being
awake. I will not promise an overnight pager I cannot honor.

**Do I get uptime numbers I can show a client or a boss?**
Yes. The dashboard tracks every incident and reports time-weighted uptime across
24 hour, 7 day, and 30 day windows, along with how long each outage lasted and
the average time to recovery. If you resell this, it white-labels to your brand.

**Do you work with my existing n8n, Make, or cron jobs?**
Yes. I deploy the monitoring and scheduling layer around whatever you already
run, and I can move scheduled jobs onto your own server if you want off metered
billing. A one-line check-in at the end of any existing job lets the dead-man's
switch catch it the moment it stops running.

**Can you track what my AI features actually cost and whether they still work?**
Yes, and this is where most teams are flying blind. Your app reports each model
call to the stack, which prices it from a table you control, so you see live
dollar cost per prediction, cost per thousand, and projected monthly spend at
your volume, split by model and by cheap, standard, or hard tier. It tracks your
prompt versions and flags when a change makes outputs longer, pricier, lower
quality, or more error prone, and it runs your labeled test sets to score
accuracy and hallucinations so a quality regression pages you like any other
outage. It works with Claude or any OpenAI-compatible API, and no key is ever
stored in the config or the code.

Message me with what you are running today and I will show you the live demo.
