# OpsWatch: your automations, deployed on your own server and watched around the clock

I deploy a self-hosted operations stack on your VPS that runs your jobs on a
schedule, checks that they keep working, and messages you the second something
breaks. You own the server and the code. There is no per-task billing and no
subscription to a tool that can change its prices or shut a feature off.

Live and working on your box in about five business days.

## The problem I keep getting hired to fix

Most automations fail quietly. A sync stops running, a script errors at 2am, an
API starts returning empty results, and nobody notices until a customer or your
boss does. By then you are doing damage control instead of a five minute fix.

The usual setups make this worse, not better:

- Your Zapier or Make bill climbs every month and you still have no idea when a
  workflow silently stops firing.
- The person who built your automations left, and now they are a black box no
  one wants to touch.
- You are paying for five different tools (a scheduler, a monitor, an alerter)
  that do not talk to each other.

You do not need another SaaS subscription. You need automations that run on
infrastructure you control and that tell you the moment they break.

## What OpsWatch is

A compact operations stack I install on your server:

- **Scheduler** that runs your jobs on an interval or at a set time each day,
  with automatic retries and a full history of every run.
- **Monitors** that continuously check the things that matter: an endpoint is
  responding, disk space is healthy, and a job has actually run recently. That
  last one is the check that catches a job which silently stopped.
- **Alerting** that messages you on the channel you already use (Slack,
  Telegram, Discord, Teams, or email) the instant something changes state, and
  again when it clears. One alert per incident, not a flood.
- **A status dashboard** that shows every job, every monitor, and a live alert
  feed on one page, so you can answer "is everything running right now" at a
  glance.

It runs on standard Python with no external dependencies, so it installs on a
plain Linux box and keeps running whether or not I am around.

## What you walk away with

- Everything running on a server you own, with no per-task fees.
- A documented runbook so you or anyone on your team can operate it.
- Alerts that reach you before your customers feel the problem.
- One flat cost in place of a stack of metered subscriptions.

## Pricing

| | **Deploy** | **Deploy + Managed**  ·  most popular | **Fully Managed** |
|---|---|---|---|
| One-time setup | $2,400 | $4,800 | $9,500 |
| Monthly | none | $750/mo | $1,800/mo |
| Jobs and monitors configured | up to 5 jobs, 3 monitors | up to 10 jobs, 6 monitors | unlimited, built to fit |
| Notification channels | 2 | 2 | all you need |
| Runbook + walkthrough | yes | yes | yes |
| Fix-it-free window | 14 days | included while managed | included while managed |
| Monitoring + alert response | you run it | next business day | same business day, priority |
| Monthly tuning + changes | not included | up to 3 changes/mo | up to 10 changes/mo |
| Patching + security updates | not included | included | included |
| Monthly health report | no | yes | yes |
| Custom job authoring + integrations | no | no | yes |
| White-label for resale | no | no | yes |

The managed tiers include your first month of monitoring free, and run on a
three month minimum after that.

For context: the managed tier runs less than a couple of contractor days a
month, and it folds the separate scheduler, monitoring, and alerting
subscriptions you are probably paying for now into one line.

## See it work before you pay anything

I will show you the stack running live: scheduled jobs firing, a monitored
service going down, and the alert landing in chat within seconds, then clearing
on its own when the service comes back. You watch the failure get caught in real
time. That demo is the whole pitch, and it takes ten minutes.

## My guarantee

Your stack is deployed and working within five business days of getting access,
or you do not pay the final milestone. I bill 50% to start and 50% once it is
verified running on your box. Every delivery includes a 14 day window where I
fix anything in the agreed scope at no charge.

## How it works

1. **Short call.** You tell me what needs to run and where it breaks today. I
   confirm scope and send a fixed quote. No hourly meter.
2. **I deploy.** I harden your server, install the stack, wire your jobs,
   monitors, and alert channel, and test every alert by triggering it on
   purpose.
3. **Handoff.** You get admin access, a runbook, and a walkthrough. You own all
   of it.
4. **Optional managed.** I keep watching it, respond to alerts, and make your
   monthly changes, so it stays healthy without you thinking about it.

## Questions I get

**Is self-hosting going to become my problem to maintain?** No. On a managed
tier I run it and watch it. You get the ownership without the babysitting.

**What if you disappear?** You own the server and the code, and the runbook
documents everything. Nothing is locked to me. You can hand it to anyone.

**Why not just keep using Zapier or Make?** Those bill per task and own your
logic. One busy month and the bill jumps, one deprecation and a workflow breaks.
This runs on your box at a flat cost, and it watches itself.

**Can a small operation really cover uptime?** The monitoring does the watching
and pages me automatically. I am clear about response windows up front and I do
not promise an overnight pager I cannot honor. Everything is documented so
nothing depends on memory.

**What do you need from me to start?** Access to a VPS (I can recommend one for
about the price of a couple of coffees a month), the jobs you want to run, and
the chat channel you want alerts in.

## Next step

Book a short call and I will show you the live demo. If it is a fit, you will be
running on your own monitored stack inside a week.
