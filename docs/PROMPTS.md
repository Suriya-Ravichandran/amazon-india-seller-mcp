# Prompt Library

Copy-paste prompts for Claude Desktop, organised by what you are trying to find out.

You never name the tool — Claude picks it. Just describe the question the way you
would to a person. Every prompt below was written against the 20 tools this server
provides.

---

## Contents

1. [The 10-minute product decision](#the-10-minute-product-decision)
2. [Finding products to sell](#finding-products-to-sell)
3. [Checking demand](#checking-demand)
4. [Sizing up competitors](#sizing-up-competitors)
5. [Money: profit, revenue, launch budget](#money-profit-revenue-launch-budget)
6. [Sourcing and suppliers](#sourcing-and-suppliers)
7. [Building the listing](#building-the-listing)
8. [Live data and scraping](#live-data-and-scraping)
9. [Chained workflows](#chained-workflows)
10. [Prompts that make Claude show its working](#prompts-that-make-claude-show-its-working)
11. [Writing your own prompts](#writing-your-own-prompts)

---

## The 10-minute product decision

If you only run one thing, run this:

```text
I have ₹20,000 to start selling on Amazon India. Screen these ideas and tell me
which one to pursue: silicone sink strainer, cable organizer, spice rack,
laundry bag, mobile stand.

For the winner, check whether demand is evergreen, whether any new sellers are
succeeding, and what my profit would be if it costs me ₹120 and I sell at ₹399.
```

Claude will chain `find_product_opportunities` → `analyze_evergreen` →
`analyze_competitors` → `calculate_profitability` and give you one answer.

---

## Finding products to sell

```text
Find beginner-friendly Amazon India products under ₹20,000 investment.
```

```text
Screen these product ideas and rank them by opportunity score:
kitchen drawer organizer, pet hair remover, desk cable clip, shoe rack.
```

```text
Screen my product ideas but use a stricter filter: at least 35% margin and
under 300 grams.
```

```text
I want a product between ₹199 and ₹699 that sells year-round and is easy to
source in Chennai. Suggest some and screen them.
```

```text
Research silicone sink strainers on Amazon India and score the opportunity
out of 100.
```

```text
Compare a silicone sink strainer against a stainless steel one — which is the
better first product for a beginner, and why?
```

---

## Checking demand

```text
Analyze the demand for silicone sink strainers on Amazon India.
```

```text
Is a silicone sink strainer evergreen or seasonal? Show me the 5-year trend.
```

```text
Which of these is most evergreen: umbrella, sink strainer, woolen sweater?
```

```text
How many units are people actually buying for "cable organizer" right now?
```

```text
Are there at least 3 listings selling 300+ units a month for kitchen drawer
organizers? If not, tell me the market is too small.
```

```text
Should I launch a raincoat business on Amazon India? Check the seasonality
before you answer.
```

---

## Sizing up competitors

```text
Analyze the competition for kitchen drawer organizers.
```

```text
Are any new sellers succeeding in the cable organizer market? I want to know
if a newcomer can realistically rank.
```

```text
Who are the top 5 sellers for "soap dispenser" by revenue, and how much is
each making per month?
```

```text
How many reviews do I need before I can compete for "mobile stand"?
```

```text
Show me the weakest listings for "spice container set" — the ones I could beat
on images, rating or review count.
```

```text
How concentrated is the market for vegetable choppers? Is it a few big players
or lots of small ones?
```

```text
Compare the competition for "sink strainer" against "silicone sink strainer" —
is the long-tail keyword easier to enter?
```

---

## Money: profit, revenue, launch budget

```text
Calculate the profit for a ₹399 product that costs ₹120.
```

```text
Calculate profitability for a ₹449 product: ₹150 product cost, ₹15 packaging,
280 grams, FBA, Home & Kitchen, 5% expected returns.
```

```text
Compare FBA vs Easy Ship vs Self Ship for a 280 gram product selling at ₹449.
Which keeps the most margin?
```

```text
What price do I need to charge to hit a 30% margin if my product costs ₹120?
```

```text
What monthly revenue would a ₹399 product at BSR 3,500 make in Home & Kitchen?
```

```text
A competitor shows "500+ bought in past month" at ₹349. What is that listing
earning, and what would I earn at the same volume if my cost is ₹110?
```

```text
Plan a ₹20,000 launch for a ₹399 sink strainer that costs ₹120. How many units
do I order, and what do I spend on ads?
```

```text
At ₹399 selling price and ₹120 cost, how much can I afford to spend per order
on advertising and still stay profitable?
```

---

## Sourcing and suppliers

```text
Find suppliers for cable organizers in Chennai or Tamil Nadu.
```

```text
Where in Parry's Corner would I source silicone kitchen accessories?
```

```text
I want to source a silicone sink strainer. Give me sourcing channels and the
exact questions I should ask a supplier before paying anything.
```

```text
Search the web for silicone sink strainer manufacturers in India and tell me
which results look like real manufacturers versus resellers.
```

```text
What should I check before paying a supplier I found on IndiaMART?
```

---

## Building the listing

```text
Research keywords for a reusable silicone food storage bag.
```

```text
What backend search terms should I use for a sink strainer? Include Hinglish
spellings.
```

```text
Generate an Amazon India listing for a reusable silicone food storage bag.
Features: leak proof, reusable, BPA free.
```

```text
Find customer complaints about manual soap dispensers, then write a listing
that leads on fixing the top complaint.
```

```text
What do buyers complain about most for vegetable choppers, and what should I
tell my supplier to change?
```

```text
How good is competitor imagery for "cable organizer"? What should my 7 images
show?
```

```text
Write 5 bullet points for a silicone sink strainer that address the most common
complaints in the category.
```

---

## Live data and scraping

```text
Check the scraper status. Is live data switched on?
```

```text
Scrape live Amazon India results for "silicone sink strainer" and show me which
listings have purchase badges.
```

```text
Scrape ASIN B0D41Y1BHN and tell me its BSR, weight, images and estimated sales.
```

```text
Scrape the live search page for "cable organizer", then analyze those
competitors for me.
```

```text
Search the web for "silicone sink strainer wholesale Chennai".
```

If a scrape is blocked, Claude will tell you — that is Amazon's bot protection,
and this server stops rather than working around it. Ask:

```text
The scrape got blocked. What are my options?
```

---

## Chained workflows

Claude runs these as a sequence. Paste the whole block.

### Full product validation

```text
I'm considering selling a silicone sink strainer on Amazon India.

1. Score the opportunity.
2. Check whether demand is evergreen or seasonal.
3. Tell me if new sellers are succeeding in this market.
4. Calculate my profit at ₹399 if it costs me ₹120.
5. Give me a go / no-go with your reasoning.

Flag clearly which numbers are live data and which are estimates.
```

### From idea to launch plan

```text
Screen these ideas: sink strainer, cable organizer, spice rack.
Take the winner and:
- research its keywords
- generate a full Amazon India listing
- plan a ₹20,000 launch assuming a ₹120 product cost and ₹399 price
```

### Competitor teardown

```text
For "kitchen drawer organizer":
- who are the top sellers and what are they earning
- how many reviews do I need to compete
- what do buyers complain about
- what would my listing need to do differently to win

Then summarise as a one-page plan.
```

### Weekly market check

```text
Check the current demand, purchase signals and competition for cable
organizers. Compare it to what you told me last time if you have it stored.
```

---

## Prompts that make Claude show its working

This server labels every number with its source and trustworthiness. Use these to
make Claude surface that instead of just quoting figures:

```text
Research cable organizers, and for every number tell me whether it is Live,
Estimated or Demo data.
```

```text
Is this real Amazon data or demo data right now?
```

```text
What assumptions went into that profit calculation?
```

```text
You said 400 units a month. Where did that number come from, and how wide is
the uncertainty?
```

```text
Which parts of this analysis should I verify myself before spending money?
```

```text
Don't give me a recommendation yet. First tell me what data you're missing.
```

---

## Writing your own prompts

**Give the numbers you know.** "Calculate profit for a ₹399 product" works;
"calculate my profit" makes Claude guess.

**Name the marketplace when it matters.** Everything defaults to `amazon.in`, but
saying "on Amazon India" keeps answers India-specific.

**Ask for the verdict, not just the data.** "Should I launch this?" produces a
recommendation; "analyze this" produces a table.

**Ask it to challenge you.** "Give me three reasons this product would fail" is
often more useful than another opportunity score.

**Chain in one message.** Numbered steps in a single prompt beat five separate
messages — Claude keeps the context between tools.

**Set your own thresholds.** The defaults are ₹199–₹699, under 500 g, 30% margin,
300 units/month. Override any of them in the prompt: "use a 40% margin target and
a 250 gram limit".

---

## Related

- [`SETUP.md`](SETUP.md) — installation and Claude Desktop configuration
- [`SCRAPING.md`](SCRAPING.md) — live data sources and guardrails
- [`../README.md`](../README.md) — the full tool reference
