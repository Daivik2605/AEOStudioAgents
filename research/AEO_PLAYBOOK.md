# AEO/GEO Playbook — what actually works

**Research date: 30 September 2026.** Reference document, not a decision document. `PLAN.md` is still the source of truth. Section 11 lists the places where this research disagrees with `PLAN.md` and needs a decision.

Re-check this document every quarter. Several findings here have a shelf life of weeks.

---

## 0. How to read this

**Three things before any of the numbers.**

**(a) This field's public writing is heavily polluted.** A large share of what ranks for these topics is AI-generated SEO content recycling a handful of real studies with the numbers mangled, no named author and no method. Several "2026 studies" assert precise percentages with untraceable provenance. Every number below is tagged, and anything untagged or Tier D must never reach a client deliverable.

**(b) Evidence tiers used throughout.**

| Tier | Meaning |
|---|---|
| **[A]** | Primary source, official documentation, court testimony, academic preprint, or an industry study with disclosed sample and method |
| **[B]** | Named author, partial method, or a vendor with a commercial interest in the result |
| **[C]** | Widely repeated, weak or no evidence |
| **[D]** | Untraceable provenance — **never quote** |

**(c) Vendor research dominates.** Almost all of this comes from companies selling AI visibility tools. The two best-designed controlled experiments in the field (Ahrefs on schema, Ahrefs on self-promotion) both produced results *less* favourable than the industry narrative. Expect the untested tactics to disappoint too.

---

## 1. The mental model: four failure modes

"AI doesn't mention us" is four different problems with four different fixes. Diagnosing which one applies is the first job of an audit.

| # | Failure | What it looks like | Fixable? |
|---|---|---|---|
| 1 | **Blocked from the index** | AI crawler gets a 403 or is disallowed in robots.txt | Yes, immediately. Most common self-inflicted cause |
| 2 | **Indexed but never retrieved** | Site is crawlable, never appears in the candidate set | Yes — match the sub-queries, not the head term |
| 3 | **Retrieved but not cited** | In the candidate pool, never promoted to a source | Yes — passage quality and distinctiveness |
| 4 | **Not in memory, and the query triggers no search** | Informational queries answered from the model's weights | Barely. Years, if ever |

**The funnel is brutally narrow.** Resoneo instrumented 1,249 real ChatGPT conversations [A]: **58,000 URLs retrieved → 7,600 promoted to "sources" → 5,000 cited in text → 760 actually opened.** Per median answer: ~20 pages listed, 5 promoted, 3 cited, 0 opened. A page the model *opens* is cited **74%** of the time; a page merely retrieved, **7%**.

**Retrieval beats memory, by a lot.** Fractl ran 6,000+ controlled retrieval runs across 9 models, 128 companies [A]:
- Page present in the retrieved set → the company appeared in **85–100%** of answers.
- Page absent → training memory rescued it in **0–17 of 48** runs.
- Training-data mention volume explains only **~3.5%** of what a model knows about a company.
- Below 1,000 training mentions, models "knew" a company 83% of the time but **named it only 8%** of the time.

**Whether a search happens at all is decided by query intent, not by the business.** Cloro, 634 responses, logged out, search not forced [A]: ChatGPT ran a web search on **86.5% of commercial prompts** and **0.9% of informational prompts**. Six informational categories (nature, science, history, geography, definitions, how-things-work) produced zero searches.

**This is the good news.** Commercial queries — the ones that matter — are overwhelmingly retrieval-grounded, so they are fixable by fixing sources. Failure mode 4 mostly affects queries where the business's identity was never at stake.

**Being cited and being named are different problems.** Semrush, 3,981 domain appearances [A, small n=115 prompts]: **61.7% were "ghost citations"** — linked as a source but the brand never named in the answer text. Only 13.2% were both. Per engine: Gemini 83.7% mention rate but 21.4% citation rate; ChatGPT 20.7% mention, 87% citation. Query shape dominates everything — short conversational queries produced ~100% mention rates, long structured prompts **2–3%**, a 30–50× gap on identical topics.

**Diagnosing memory vs retrieval in practice:**
1. Citations present? No citations ≈ no retrieval.
2. Server logs: `ChatGPT-User` = a live user-initiated fetch. `OAI-SearchBot` = index building. Neither, while being named = memory.
3. `utm_source=chatgpt.com` on inbound traffic — but note it is **absent** on pages the model opened and read, so the highest-value citations are invisible in analytics.
4. Ask the same question with and without search forced. If the brand list moves, the baseline was memory.

---

## 2. Per-engine cheat sheet

| Engine | Index | Classic rank still matters? | Local data | Notes |
|---|---|---|---|---|
| **ChatGPT** | Own index (Labrador ~64% of free instant) + scraped Google; Microsoft a documented partner | **Barely.** 8.0% top-10 overlap. 50% of its top-3 citations have zero Google presence | **Yelp** — 95.8% of local runs after the July 2026 licence | Strongest freshness bias (+458 days vs organic). Strips JSON-LD before reading. Rejects pages over 4 MB. Meta descriptions ignored |
| **Perplexity** | Own index, 200B+ URLs, **chunk-level** | Most of any non-Google engine: **28.6%** | Yelp, MapQuest | Cites most sources (16.4/answer). Officially documents span-level retrieval and cross-encoder reranking. Best diagnostic surface — always retrieval-grounded |
| **Google AI Overviews** | Google index via **FastSearch/RankEmbed**, not the link ranker | Fell **76.1% → 37.9%** (Jul 2025 → Jan 2026); ~43% mid-2026 | **Google Business Profile** | 59.4% of AIOs name no brand at all. Highest UGC share (18%) |
| **Google AI Mode** | Same, wider fan-out | **14%** URL | **GBP panels — now the #2 cited domain**, up 8.4× | 3.3 entities/answer vs AIO's 1.3. Most volatile surface measured |
| **Claude** | Brave Search + turbopuffer (vector DB, added May 2026) | No study exists | No data | `cited_text` capped at **150 characters**. Most conservative: underclaims features 160× vs overclaims 10× — more likely to omit you than misdescribe you |
| **Copilot** | Bing | 14.0% vs Bing's top 10 | — | **Only engine with first-party citation data** — Bing Webmaster Tools AI Performance report, free, includes the actual retrieval queries |
| **Grok** | Undisclosed | — | — | Reddit 16.3%, YouTube 15.1%, Facebook 13.9%. **X.com is #12 at 1.4%** — the "post on X" advice is a myth |

**AI Mode and AI Overviews are different engines.** 13.7% URL overlap across 730,000 response pairs, yet **86% semantic similarity** [A]. They say the same things from completely different sources.

**Query fan-out is the mechanism behind the decay of classic ranking.** Officially confirmed by Google for both surfaces. Measured: ChatGPT free instant **median 1** sub-query (single search >80% of the time); paid thinking went **6 → 1.90 between July and August 2026** — a 3× swing in one month, apparently a cost decision. Nobody can enumerate Google's real sub-queries except indirectly through the Gemini API's `google_search_call`. Treat every commercial "fan-out keyword" tool as inference.

---

## 3. The levers that work, in priority order

### P0 — gates. Everything below is worthless if these are broken.

**1. Can AI crawlers actually reach the site?** This is the highest-probability silent failure, and it is invisible from a browser.

```bash
curl -A "Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)" -I https://example.com/
curl -A "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)" -I https://example.com/
curl -A "Mozilla/5.0 (compatible; Claude-SearchBot/1.0)" -I https://example.com/
```
Expect `200`. A `403` means invisible.

**Cloudflare is the landmine.** [A] On **15 September 2026** Cloudflare flipped its defaults: training and agent crawlers blocked by default on ad-bearing pages, for new customers, new sites created by existing customers, and existing free-plan customers who had never set a preference. Search bots remain allowed. Three separate systems can block AI bots and all three need checking:
- **AI Crawl Control** (Security → Bots) — "Block AI bots" toggle plus per-crawler rules
- **Managed robots.txt** — emits `Content-signal: search=yes, ai-train=no, use=reference` by default, i.e. it sets `ai-train=no` for you
- **Bot Fight Mode / Super Bot Fight Mode** — challenges non-browser clients; AI crawlers cannot solve challenges, so they get 403s

Fix with a WAF custom rule (custom rules evaluate before managed rules): match the retrieval bot user agents, action **Skip** → WAF Managed Rules + Bot Fight Mode + Block AI bots. Re-check AI Crawl Control after 24–48h.

**2. Every provider splits training bots from retrieval bots.** [A] You can refuse to be training data and stay fully citable. Allow these — they cite and link:

`Googlebot` · `Bingbot` · `OAI-SearchBot` · `ChatGPT-User` · `Claude-SearchBot` · `Claude-User` · `PerplexityBot` · `Perplexity-User` · `Applebot`

Training-only, a business decision: `GPTBot` · `ClaudeBot` · `Google-Extended` · `Applebot-Extended` · `Meta-ExternalAgent` · `CCBot` · `Amazonbot` · `Bytespider`

**Never block `Googlebot` or `Bingbot`.** Google states explicitly that `Google-Extended` does not affect Search inclusion or rankings. Blocking `GPTBot` does **not** remove you from ChatGPT — search uses `OAI-SearchBot`.

**3. Server-rendered HTML.** The highest-impact single fix on this list.

[A] Three independent methods agree: **GPTBot, OAI-SearchBot, ClaudeBot, Claude-* and PerplexityBot's own fetch do not execute JavaScript.** Googlebot and Gemini do. Vercel (~1 billion crawler requests): "The assumption that 'if Google can index it, AI search can too' is false." Glenn Gabe's case study on a client-rendered site: ChatGPT "could not read the content," Perplexity returned "Access Denied," Claude returned the URL "without any visible content."

A client-side-rendered React/Vue SPA is **effectively invisible to ChatGPT, Claude and Perplexity while ranking fine in Google.**

```bash
curl -sL https://example.com/ | grep -i "<h1\|street\|telephone"
```
If the H1 and the address are not in that output, no AI crawler sees them.

**4. Snippet controls are AI controls.** [A] `nosnippet`, `data-nosnippet`, `max-snippet`, `noindex` all govern AI Overviews and AI Mode eligibility. Audit for a stray `max-snippet:0` — it silently removes you from AI answers while you still rank.

**5. Register Bing Webmaster Tools and submit via IndexNow.** The cheapest unexploited lever for ChatGPT and Copilot presence, and the AI Performance report is the only first-party citation data that exists anywhere.

### P1 — high confidence

**6. Explicit entity statements in visible HTML.** "Acme Plumbing is a licensed plumbing contractor based in Lyon, France, serving Lyon, Villeurbanne and Vénissieux." Advanced Web Ranking hand-coded 112 passages [A, small n]: **96% of cited passages named the entity at first mention vs 82% of uncited**. Repeat the business name per section; never carry identity on pronouns or the nav bar. This is also the only reliable way to get `areaServed`-type facts somewhere a model will actually read them.

**7. Distinctiveness over consensus.** The sharpest finding in the 2026 literature [A]: pure consensus restatement appeared in **61% of cited passages but 82% of uncited** ones. Advanced Web Ranking's conclusion — *"writing the standard answer in the standard way is now a strategy for feeding AI answers without being named in them."* Interchangeable content gets **absorbed**; distinctive content gets **attributed**. First-party data, your own prices, your own case numbers, named staff, specific local detail.

**8. Quotes, statistics, sources.** The GEO paper (Aggarwal et al., KDD 2024) [A] measured per-technique uplift: Quotation Addition **+41%**, Statistics Addition **+33%**, Fluency Optimization **+28%**, Cite Sources **+27%**, Keyword Stuffing **−8%**. Independently replicated at larger scale (geo-citation-lab, Apr 2026, 21,143 citations): code snippets **+76.9%**, statistical numbers **+61.5%**, definition markers **+57.3%**, comparison content **+55.3%**; top-quartile pages carry **12× more headings**.

**Two caveats that get dropped every time this paper is cited.** Effects are domain-conditional, and *"GEO is especially helpful for lower ranked websites"* — top-ranked sources sometimes **lose** visibility. It is a leveller, not a multiplier for incumbents. Also: the magnitudes come from a research-built engine on 2023-era GPT-3.5. The direction has replicated; the percentages are not promises.

**9. Visible dates and real named authors.** 80% of cited passages carried a 2025–2026 date vs 53% of uncited [A]. Genuine `lastmod` — falsified dates are discounted.

**10. Third-party mentions, not backlinks.** Ahrefs, 75,000 brands [A], Spearman correlations with AI Overview brand visibility:

| Factor | ρ |
|---|---|
| **Branded web mentions** | **0.664** |
| Branded anchor text | 0.527 |
| Branded search volume | 0.392 |
| Domain Rating | 0.326 |
| Referring domains | 0.295 |
| **Backlinks** | **0.218** |

Mentions correlate ~3× as strongly as raw backlinks. Surfer independently found ρ=0.41 between a brand's recommendation position and the share of *cited source pages* that mention it, across 26,573 AI calls [A].

**Carry the caveats.** Ahrefs says so themselves: correlation ≠ causation, and all correlations were moderate to weak. Large brands get more mentions *and* more citations because they are large. And the sample was filtered to DR>40 and ≥800 searches/month — **it excludes exactly the small businesses we work with.**

**The defensible claim:** mentions appear to matter at least as much as links and probably more, and the mechanism — LLMs read text, not `href` attributes — is consistent with that. We cannot promise a mention causes a citation.

**11. Earned media is the dominant input.** Muck Rack, 25M+ links across three editions [A]: **earned media = 84%** of AI citations (82–89% across editions), journalism specifically 27%, **paid/advertorial 0.3%** — engines filter it heavily. 20,000+ distinct journalism outlets cited. NYT and Reuters appear in no industry's top 3 because they block AI crawlers; **Axios is top-3 in 13 of 17 industries**. Determinants are crawlability and licensing as much as prestige.

**Only 2% overlap** between the journalists PR teams pitch most and the journalists AI cites most. The standard media list is near-worthless here; targets must come from citation data per vertical.

**12. Self-promotional content works for unknowns only.** Ahrefs, 9,886 AI answers tracked daily over four months [A]:
- New, unknown brand: **82%** of new mentions in previously empty answer slots came from answers citing the test pages. **This is the best evidence that publishing can make an unknown entity enter AI answers at all.**
- Established brand: only **6%**; the other **94%** came from third-party sources.
- **Backfire: 43%** of answers that cited the self-promoting pages never mentioned the brand being promoted and recommended competitors instead.

So a "best X" page of your own is a defensible cold-start tactic and nothing more, with a ~40% chance of handing the answer to a competitor. Getting into **someone else's** list is the higher-value move.

### P2 — cheap, correct, low expected AI return

**13. Structured data.** Do it. Do not expect AI citations from it.

Ahrefs ran a matched difference-in-differences study [A] — 1,885 pages that added JSON-LD, ~4,000 matched controls, 30-day windows: **AI Overviews −4.6%, AI Mode +2.4%, ChatGPT +2.2%.** The last two are indistinguishable from noise. Google's own documentation: *"There's also no special schema.org structured data that you need to add."* And there is a mechanism — ChatGPT strips JSON-LD and converts HTML to Markdown before the model reads it.

**Do LLMs read JSON-LD at all?** Two controlled tests reconcile as *"ingested as text, not parsed as data."* SearchVIU: **no engine extracted a value that existed only in JSON-LD**. Williams-Cook's "duck experiment": both ChatGPT and Perplexity returned an address that existed only in JSON-LD — but the schema was deliberately invalid, proving they were not validating or parsing it. So a model *can* pick a fact out of it when it is the only candidate, but it is not semantically privileged and loses to visible text.

**Ahrefs' own caveat matters most:** every page in their sample already had 100+ AI Overview citations. The study says nothing about whether schema helps a brand that is **not yet known**. That is the genuinely open question and the most defensible pro-schema argument available.

Implement: `Organization` (rich — `name`, `alternateName`, `legalName`, `url`, `logo`, `description`, `address`, `telephone`, `sameAs`, `foundingDate`), the correct specific `LocalBusiness` subtype where premises exist (required: `name`, `address`; recommended: `geo`, `openingHoursSpecification`, `telephone`, `priceRange`), `BreadcrumbList`, `Article` on editorial, `Product` on commerce. Validate. Then stop.

**14. `llms.txt` — effectively unread.** [A] Ahrefs analysed server logs across 137,000 domains: **97% of llms.txt files received zero requests in May 2026.** Of requests that did happen: SEO audit tools 21.7%, unidentified bots 14.9%, general crawlers 13.1%, GPTBot 4.51%, ClaudeBot 0.80% — **all AI retrieval bots combined ~1.1%**. Originality.ai tracked 3M+ sites: adoption grew 8.8× while "almost nothing is reading it."

Google Search says it is not needed. John Mueller compared it to the keywords meta tag and said no AI services used it and bots did not request the file. Note the asymmetry: OpenAI, Anthropic and Google all publish llms.txt for their own developer docs, and none of them recommends it to site owners or commits to reading it.

**Verdict:** ~30 minutes, passes a Lighthouse audit, no downside. Ship it as cheap insurance, label it honestly as unproven, and spend zero further time on it.

**15. Content negotiation (`Accept: text/markdown`).** The best-evidenced of the new techniques — Checkly's data shows Claude Code, Cursor and other coding agents actually send this header. Standards-based, not cloaking. But the same CDN analysis found **zero** `.md` requests from GPTBot, ClaudeBot or PerplexityBot. This serves dev agents, not search crawlers. Size the effort accordingly.

### P3 — monitor only

**WebMCP** — W3C Community Group draft, Chrome 146 Canary behind a flag, already audited by Lighthouse 13.3's Agentic Browsing category. Worth prototyping if the site has booking or quoting flows. Not a standard. Despite the name it is not MCP.

**NLWeb** — technically sound, commercially unproven. **No evidence any AI assistant queries third-party NLWeb endpoints.**

**Cloudflare Pay Per Use** (30 Sep 2026) — payment when content is *used in an answer* rather than when fetched. Days old. Watch, do not architect around it.

---

## 4. Off-site: where the citations actually come from

**Citation share is diffuse.** Profound, 680M citations [A]: even the single most-cited domain on the most concentrated engine takes **under 8%**; on AI Overviews the leader takes **2.2%**. The long tail dominates.

**Reddit and YouTube are the only domains in the top 5 of all five major engines** (Peec, 30M sources) [A/B]. Overall top 10: Reddit, YouTube, LinkedIn, Wikipedia, Forbes, G2, Yelp, Facebook, Medium, TechRadar.

**But engine weightings are a vendor policy decision, revocable overnight.** The most important finding in this section: Semrush tracked 230,000+ prompts weekly [A] and found ChatGPT citing **Reddit in close to 60% of responses in early August 2025, collapsing to ~10% by mid-September 2025**. Wikipedia fell ~55% → ~20% over the same window. Nothing changed on Reddit. **Any strategy built on "get cited on domain X because X is currently #1" is building on sand.**

**Google Business Profile is now a literal AI citation surface.** Profound, 32M+ instances, Apr–Jun 2026 [A]: google.com's citation share in AI Mode rose **8.4× in ~2.5 months to become the #2 most-cited domain**, "almost entirely from Google Business Profiles and Product Knowledge Panels." For a local business this is the highest-leverage off-site asset in Google's AI surfaces.

**BrightLocal, 1.9M citations across 116,670 domains** [A]: GBP **28.63%**, Yelp 9.53%, Facebook 2.23%, TripAdvisor 1.79%. Split by engine: GBP leads Google's own surfaces (AI Mode 455,123 citations); **ChatGPT's dominant local source is Yelp, in 80% of answers.**

**Review platforms carry disproportionate weight per link.** SE Ranking, 22,729 AI Overviews [A]: ~34.5% pull from a review site; review platforms are only 8.5% of all links yet occupy 3 of the top 5 most-cited domains. Share of review-platform citations: **Gartner Peer Insights 26.0%, G2 23.1%, Capterra 17.8%, Software Advice 12.8%, TrustRadius 8.3% — 88% combined.** AlternativeTo, SaaSHub and FinancesOnline: **0%, never cited.** Being on the long-tail platforms is close to worthless; being on the top five is close to mandatory.

Counter-intuitive and useful: **"best X" queries are the *least* likely to cite review platforms (17.1%)**, vs explicit "review" queries at 49.0%. On "best" queries, editorial content is what gets quoted.

**Entity establishment, honestly graded:**

| Mechanism | Evidence | Achievable for an SMB? |
|---|---|---|
| **Google Business Profile** | **[A] Strong** — cited directly at scale | Yes. The single highest-evidence action for local |
| **Claimed profiles on cited platforms** (Yelp, LinkedIn, Apple Business Connect, Bing, G2/Capterra for B2B) | [B] Directionally consistent | Yes, cheap |
| **Wikipedia** | **[A] Strong that it matters** — most-cited domain on ChatGPT | **Near zero.** WP:NCORP excludes press releases, interviews, sponsored content, routine announcements and purely local coverage. Plan as if unavailable |
| **Wikidata** | **[C] None found** | Cheap, harmless, unproven. Do not bill it as a growth driver |
| **`sameAs` chains** | **[C] None found** | Mechanistically sound for Google's KG; no controlled test |
| **NAP consistency** | **[C] No isolating study exists** | Argue the *correctness* mechanism, not a ranking one: if five sources give three phone numbers, a retrieval-based answer will sometimes surface the wrong one |

**LinkedIn deserves specific mention** — top 5 on every engine, rose fastest of any domain in late 2025, and unclaimed auto-generated company pages with wrong employee counts and locations are extremely common. High leverage, low effort.

**Crunchbase**: *anyone* registered can edit most fields, so errors recur. Monitor, do not fix-and-forget.

---

## 5. What does not work

| Claim | Verdict | Evidence |
|---|---|---|
| llms.txt gets you cited | **Myth** | 97% zero requests; Google says unnecessary |
| Schema markup lifts AI citations | **Myth at scale** | Ahrefs DiD, 1,885 pages: −4.6% / +2.4% / +2.2% |
| LLMs parse JSON-LD semantically | **Myth** | Invalid schema works identically; JSON-LD-only facts lose to visible text |
| FAQPage schema → AI answers | **Dead** | FAQ rich results stopped appearing **7 May 2026**; docs deleted 15 Jun 2026 |
| HowTo schema | **Dead** | Deprecated 8 Aug 2023 |
| `ProfessionalService` schema | **Deprecated by schema.org** | "Deprecated due to confusion with Service." Use a specific subtype |
| GBP Q&A seeding | **Gone** | Q&A removed from profiles ~3 Nov 2025; replaced by Gemini-generated "Ask Maps" |
| "Foursquare powers 60–70% of ChatGPT local" | **Measured at 0.00%** | 2,880 prompts, 12 verticals, 12 metros. Traced to a 2025 five-city Spanish study, first-ranked results only, with 87.5% caveat loss in re-reporting |
| "ChatGPT runs on Bing, optimise for Bing" | **Substantially wrong now** | Bing deindexed a site (~90K URLs) in July 2026; ChatGPT citations *rose* to 464K responses by August |
| "Grok is X-first, post on X" | **Myth** | X.com is #12 at 1.4% mention share |
| Rank #1 and you're in the AI answer | **Myth** | Position-1 citation probability 24.9%–57.9% depending on study; position 1 is only ~8% of all citations |
| Keyword stuffing for LLMs | **Actively harmful** | −8%, the only negative technique of nine |
| Chunking into micro-pages | **Myth** | Google: "no requirement to break your content into tiny pieces" |
| "Write in a special AI style" | **Myth** | Google: "You don't need to write in a specific way just for generative AI search" |
| Extractive formatting gets you cited | **Necessary, nowhere near sufficient** | 76% of cited *and* 71% of uncited passages were extractive |
| Blocking GPTBot removes you from ChatGPT | **Myth** | Search uses OAI-SearchBot |
| Google-Extended affects rankings | **Myth** | Google states it does not |
| `ai.txt`, `<meta name="llms">`, AI info pages, HTML comments as hints | **Myth** | 397 sites total; WHATWG closed the meta proposal as "not planned"; parsers strip comments |
| User-agent sniffing to serve AI-specific content | **Myth + policy risk** | That is cloaking. Use HTTP content negotiation instead |
| Core Web Vitals drive AI citations | **Unproven** | No credible study. CLS has the only documented agentic rationale, and only for screenshot-taking agents |
| "Google renders JS so AI is fine" | **Myth** | Explicitly refuted by Vercel |
| Seeding mentions on blogs and forums | **Myth** | Google: "Seeking inauthentic mentions… isn't as helpful as it might seem" |
| AI referrals convert better than organic | **Not supported** | Best-designed study: p = 0.794 |

**The meta-myth worth naming:** much of the "GEO evidence" circulating in 2026 is AI-generated content citing other AI-generated content. Demand a named author and a published method before believing a percentage.

---

## 6. Measurement: what we can and cannot honestly claim

This section matters more than any tactic, because it determines what we can put our name to. **Most of what the industry ships is reported at a granularity its sampling cannot support.**

### 6.1 Non-determinism is irreducible

Thinking Machines Lab ran **1,000 completions at temperature 0** of one prompt and got **80 unique completions** [A]. The cause is not sampling — it is batch-size variability changing floating-point reduction order in inference kernels. Server load alters batch composition, which alters accumulation order, which alters logits.

**Non-determinism on a shared production endpoint is a function of other people's traffic.** You cannot configure it away and temperature=0 does not fix it.

### 6.2 How much do answers actually move?

Schulte et al. (University of St. Gallen, arXiv:2604.07585) [A] — the best methodology paper in the field. 32 prompts, 4 engines, up to 10 same-day repeats:

| Comparison | Brand-set Jaccard | Source-set Jaccard |
|---|---|---|
| Same-day reruns | **0.33–0.48** | 0.32–0.43 |
| Consecutive days | 0.45–0.59 | 0.34–0.42 |

**Read that carefully: same-day and next-day overlaps are nearly identical.** Day-to-day movement is dominated by within-day randomness, not by the engine changing. **A day-over-day change in a dashboard is, by default, noise.**

Corroborating:
- SE Ranking ran 10,000 AI Mode queries three times the **same day**: only **9.2% of URLs** appeared in all three runs [A].
- SparkToro/Gumshoe, ~2,961 runs with 600 volunteers: probability of the same brand list twice **under 1 in 100**; same list in the same order **~1 in 1,000**. But **presence was far more stable than order** — brands appeared in 60–90% of responses while their rank bounced [A].
- Clovion, 69,120 multi-turn conversations: adding one realistic buyer qualifier ("for a small team") left only **28%** of the original recommendations standing — **62% of brands vanished** [A].
- **Paraphrase sensitivity is worse than rerun variance** [A]: same-prompt rerun Jaccard 0.50–0.61; cosmetic reword **0.288**; constraint-adding reword **0.135**. *"The prompt string, not the underlying buyer intent, is the dominant input to which brands surface."*

### 6.3 How many repeats — the actual numbers

Schulte et al.'s bootstrap-derived figures, the only properly derived ones available [A]:

| Purpose | Same-day runs per prompt | 95% CI |
|---|---|---|
| Brand monitoring, minimum | **7** | ±0.158 |
| Brand monitoring, recommended | **8** | ±0.121 |
| Source-level coverage, minimum | **8** | ±0.187 |

Rolling window, per-brand series:

| Window | 95% CI |
|---|---|
| **1 day** | **±0.631** |
| 7 days | ±0.264 |
| 14 days | ±0.157 |
| 28 days | ±0.065 |

**A single day's per-brand detection rate carries essentially no information.** ±63 percentage points. This one number invalidates most dashboards on the market.

**Breadth beats depth.** Repeats within a prompt are a *cluster sample*, not independent observations: `Deff = 1 + (m−1) × ICC`, `n_eff = total / Deff`. The hard ceiling is **`n_eff → k / ICC`** — 20 prompts can never exceed n_eff ≈ 35 no matter how many times you re-run them. At a typical ICC of 0.57, the same 600 answers give:

| Design | n_eff | Margin of error |
|---|---|---|
| 20 prompts × 30 runs | 34 | ±15.4pp |
| 100 prompts × 6 runs | 156 | ±7.2pp |
| **200 prompts × 3 runs** | 280 | **±5.4pp** |

*(The statistics are textbook-correct; the specific ICC values come from an undisclosed study — treat as plausible placeholders and estimate your own.)*

**Not in conflict with Profound's "once a day is enough"** [A]. Profound measured a **portfolio aggregate over 753 prompts**, where breadth has already done the averaging; Schulte measured **per-prompt, per-brand** estimates. Both are right. At portfolio level extra repeats buy ~2pp; at per-prompt level, 1 run/day is worthless.

### 6.4 The denominator problem

A brand-free answer — one that names no business at all — is not the same as an answer that named someone else. Measured brand-free rates by prompt type: vendor-seeking **9%**, problem-aware **63%**, conceptual **91%** [B, small n]. The inflation factor from excluding them is `1/(1 − brand_free_rate)`: at 54% brand-free, a true 20% becomes **44%**.

Vendors differ on exactly this. Profound divides by responses containing ≥1 brand; Peec divides by all tracked responses. The same brand gets structurally different numbers.

**Report both denominators, always, and make brand-free rate a headline diagnostic.** It is also the best single indicator of whether a prompt belongs in the set.

Martinez (Sciences Po, arXiv:2607.14035) decomposes it properly:

> `Pr(cited) = Pr(retrieval activated) × Pr(in retrieved set | activated) × Pr(cited | retrieved, activated)`

Collapse those three and you cannot tell a retrieval failure from a ranking failure from a citation failure. **Store retrieval activation as a first-class field on every observation.**

### 6.5 API vs manual querying — this validates manual mode

Surfer SEO, **13,779 answers** across 14 model-and-method combinations [A]:
- Brand-set Jaccard, API vs scraped UI: **15.5–23.8%** raw, 21.3–31.6% after name canonicalisation
- APIs name **more** brands on all five products — ChatGPT 13.8/answer via API vs 7.9 in the UI
- Source overlap at domain level: Perplexity 26.7% (best), **ChatGPT 4.8%** (worst)
- **Unmeasurable for Gemini / AI Mode / AI Overviews** — their APIs return redirect tokens, not destination URLs
- Their conclusion: *"You cannot reliably track your AI visibility based on APIs."*

But Katman found brand *presence* agreed **100%** API-vs-UI (n=3, honest about it), and Clark & da Silva found history-free API sampling recovered **79%** of recurring brands seen in personalised sessions (n=8,609).

**Reconciliation: own-brand presence is the robust estimand; competitive set composition and citations are not.** So:

| Metric | API valid? |
|---|---|
| Own-brand mention rate | Usable, with known biases. Calibrate against a UI sample |
| Competitive share of voice | **No** — 15–32% set overlap |
| Citations / source attribution | **No**, and not even possible for Google surfaces |
| Anything localised | **No** — no exit IP, no locale |
| Anything personalised | **No** — misses 16–33pp of brand-set divergence |

**This is a direct argument for manual mode as the primary instrument**, with API collection as a separately-labelled model-prior series that is never merged into the same number.

**Also pin the reasoning mode.** GPT-5.2 Instant vs Thinking: **25.6% domain overlap**, citation rate 50% → 68%. Tools treating "ChatGPT" as one system are averaging two different citation environments.

### 6.6 Benchmarks — do not use published industry medians

There are no trustworthy published distributions. Every circulating benchmark table comes from undisclosed panels with different prompt sets, denominators, engine mixes and brand-free handling. The most useful figure in the most-quoted table is its own stated **week-to-week volatility of ±8–15pp**, which is 2–3 noise-widths wide and indicts the rest of it.

**Benchmark against a named competitor set on your own frozen prompt panel, measured identically.** That is the only comparison with a defensible error bar.

**The one benchmark worth trusting is accuracy.** Two independent designs converge:
- Searchable: **32,556 facts**, each graded by an employee of the organisation it describes — global false rate **9.2%**; **75% of brands reviewing 20+ facts found at least one error**. By engine: ChatGPT 8.1%, Google AIO 11.4%, Perplexity 11.5%. **By category: pricing 13.5%, funding 10.9%, integrations 10.6%, partnerships 9.9%, products 8.6%** [A]
- Faro Index: 503 companies, ~240 responses each — mean accuracy **90.1%** [A]

Two different designs landing on ~9–10% error is the most robust empirical result in this whole review. **Accuracy has verifiable ground truth, a stable base rate to compare against, and direct business consequence. It is the easiest metric to make defensible.**

### 6.7 Do not build a composite score

One practitioner ran the same branded prompt through six tools in one week. All six agreed the brand was mentioned. The "visibility" numbers: **100%, 90%, 75%, 16.7%.** One tool reported 90% visibility on an answer that misidentified the brand's operator — demonstrating that visibility and accuracy are orthogonal and a composite hides it.

No empirical basis for any weighting exists anywhere in the literature. **Publish the component rates.**

---

## 7. Proving a change worked

Given same-day Jaccard of 0.33–0.48 and monthly citation drift of 40–60%, **a naive before/after comparison can show any result you like.**

### 7.1 The three viable designs

1. **Page-group split** — randomly assign ~30–40 comparable pages to test and control, change only the test group. **The strongest design available**, because randomisation is genuinely possible at page level even though it is impossible at user level.
2. **Prompt-cluster comparison** — treat one cluster, hold another. Directional only; clusters are never truly comparable.
3. **Pre/post with a control prompt set** — for single-page changes; an unrelated control set absorbs model-wide shifts.

Classical A/B is impossible — you cannot serve half of ChatGPT's users a different page.

### 7.2 Difference-in-differences is the minimum bar

> `Effect = (Test_after − Test_before) − (Control_after − Control_before)`

The control term absorbs model updates, index refreshes and seasonality. Everything hinges on parallel trends, so **validate it**: enough pre-period to show test and control moved together *before* the intervention. With 14-day windows as the minimum reportable unit, that means **6–8 weeks of baseline, not two.**

Martinez requires hierarchical models with random effects for query, engine, date and source, because **treating independent generations as independent observations is pseudoreplication** — repeats within a prompt are clustered, so naive standard errors are too small and you will declare significance on noise.

### 7.3 Power

| Detect | Answers per group per period |
|---|---|
| +20pp | ~193 |
| +10pp from a 30% baseline | ~350–755 |
| +5pp | ~1,400 |
| +3pp | ~8,044 |

Paired comparisons on frozen prompt sets cut this **~3.1×** (week-over-week ρ = 0.68).

**Blunt implication: sub-5pp improvements are not detectable at any realistic budget. Any vendor reporting a 2-point gain as a result is reporting noise.**

### 7.4 Required controls

- **Verify recrawl before measuring.** Check server logs that AI crawlers actually fetched the changed pages. Measuring before recrawl guarantees a null.
- **Timeline:** 6–8 weeks baseline + 4–8 weeks post.
- **Pre-register** the metric, denominator, prompt-set version and a practical-significance threshold before collecting.
- **Hold constant across periods:** prompts, engines, model versions, locale, reasoning mode, collection method. Each of those is worth 20–40pp on its own.
- Include a **placebo arm** of comparable length, to catch "any edit shakes the cache" effects.

### 7.5 Defensible vs indefensible

**Defensible:**
> "Across a frozen set of 40 unbranded category prompts (version 3, paraphrase-expanded to 160 strings), measured on ChatGPT and Perplexity by hand, 3 runs/prompt/day, en-CA, logged out, Toronto: mention rate in the treated page-group rose from 22.4% (95% CI 18.1–27.3, n=1,680) in the 28 days to 15 Aug to 31.8% (95% CI 27.0–37.0) in the 28 days from 1 Sep. The matched control group moved 24.1% → 25.0%. DiD estimate +8.5pp (95% CI +2.1 to +14.9). Crawler logs confirm all 37 treated URLs were refetched by OAI-SearchBot between 16–22 Aug. Pre-period parallel trends held across four prior 14-day windows."

**Indefensible, and ubiquitous:**
- "Our visibility went from 34% to 41% after we published the content." No control, inside normal volatility, no n, no interval.
- Any day-over-day or week-over-week movement presented as a result.
- Any per-prompt claim at one run per day.
- Any claim where the prompt set, competitor set or engine mix changed mid-flight.
- "We improved across 8 engines" without per-engine numbers.

**A useful external check on how little survives proper design:** C-SEO Bench found only **3 of 54** method–domain combinations significantly positive, none in question-answering. SAGEO Arena found body-only optimisation **reduced** average top-20 presence by ~9%. **Most GEO interventions do not work, and the ones that appear to usually were not measured against a control.**

---

## 8. Canada, Quebec and bilingual

### 8.1 Rollout timeline [A]

| Date | Event |
|---|---|
| Week of 28 Oct 2024 | **AI Overviews launch in Canada.** English, Hindi, Indonesian, Japanese, Portuguese, Spanish. **French explicitly not supported** |
| 21 Aug 2025 | **AI Mode launches in Canada, English only** |
| 14 Oct 2025 | **AI Mode in French** — "Le Mode IA est maintenant disponible en français" |
| 26 Mar 2026 | Search Live goes global, including Canada |
| 22 Jul 2026 | AI Overviews and AI Mode launch in **France** — the French-language AIO milestone |

**Open question: are AI Overviews live in French on google.ca?** Google's availability page lists Canada among countries and French among languages, but publishes **two separate lists and no country × language matrix.** French AIOs demonstrably exist (France). The commentary asserting otherwise relies on the October 2024 blog post and does no original testing.

**This is testable in under an hour and nobody has published it.** Google.ca, French interface, Quebec location, 30–50 French commercial and local queries, desktop and mobile. Do not assert either way in client material without that test.

### 8.2 The single most actionable local finding [A]

Whitespark, 540 queries, 3 cities, 6 industries:

| Query intent | AI Overview appears | Local pack appears |
|---|---|---|
| **Local intent** ("plumber near me") | **15%** | **93%** |
| **Informational** ("how long does an eye exam take") | **92%** | 6% |
| **Hybrid** ("average cost of dental implants in Phoenix") | **97%** | 17% |

**Separate the two games.** Pure "near me" queries are still a GBP and local-pack contest. The AI-answer battleground is **informational and hybrid** queries — "how much does X cost in Toronto", "what to look for in a Y supplier in Canada". That is content work, not GBP work. For a national B2B, almost all AI upside is in the second category.

Houston plumbers sub-sample: **60% of AI citations went to third-party publishers, 40% to individual local business sites.**

### 8.3 Quebec means ChatGPT, and ChatGPT means Yelp

**NETendances 2025** (Université Laval, published 19 Mar 2026) — the best Quebec-specific data available [A]:
- **52% of Quebec internet users have used a generative AI tool**, up 19 points year over year
- 54% of those use them at least weekly
- Tool share among AI users: **ChatGPT 84% · Copilot 29% · Gemini 22%**

Combined with ChatGPT's location being **off by default** and its local grounding being **Yelp in 95.8% of runs** after the July 2026 licensing deal: **Google-first Canadian local advice under-serves Quebec.** (The combination is our inference; both inputs are documented.)

Open risk, no data: Yelp's coverage depth in Montreal and Quebec City versus US metros.

**Canada-wide business AI adoption** — Statistics Canada, Q2 2026 [A]: **19.2% of Canadian businesses used AI** in the preceding 12 months, tripled from 6.1% in Q2 2024. Professional/scientific/technical services 32.4%. Urban 21.0% vs rural 9.9%.

### 8.4 Do French queries get French sources?

Temso, 7M+ AI citations, 4 models, 6 languages [A] — share of citations in the prompt's language, for **French** prompts:

| Model | French-language source rate |
|---|---|
| Microsoft Copilot | **87.2%** |
| Google AI Overview | **82.1%** |
| ChatGPT | **72.3%** |
| Grok | **58.9%** |

**So an English-only Canadian site is not categorically excluded from French answers — it is competing for a minority slice** (~18% on AIO, ~28% on ChatGPT), and that slice shrinks on the engines with the tightest search integration.

Caveat: "French" here is almost certainly France-weighted. **No study separates `fr-CA` from `fr-FR`.**

### 8.5 Bill 96 — this is law, not a nice-to-have

For a business with a Quebec establishment, a genuine French site is a **legal obligation first and an AI-visibility asset second.** The two requirements point at the same deliverable, which makes the business case easy.

The OQLF's position on websites [A]: Charter articles **41, 52, 55, 57 and 141** apply to internet content. Where the law imposes French, other languages are permitted **provided French appears at least equivalently** — the same information in French and in any other language. Applies to businesses established in Quebec using a website to promote or sell in the province.

**Article 52 requires a French version "d'une qualité comparable."** That is a direct legal argument against raw machine translation — comparable quality is an explicit condition.

Key dates: 1 Jun 2022 (French customer service, signage predominance), 1 Jun 2023 (contracts of adhesion), **1 Jun 2025** (francization threshold drops to 25+ employees; non-French trademarks on signage need French descriptive elements at twice the space). Penalties run **per day of offence**: legal entities $3,000–30,000 first offence, rising to $9,000–90,000 for third and subsequent. Enforcement is complaint-driven plus inspection, and the OQLF publishes named infraction notices including website cases.

**Extraterritorial reach:** a non-Quebec company's site serving Quebec subsidiaries, franchisees or affiliates may be caught where there is "an important and real connection with Quebec." That is a legal question for Quebec counsel, not for an SEO deliverable.

**Implementation:** separate indexable French URLs (`/fr/` subdirectory on the main domain), reciprocal `hreflang` (`en-CA`, `fr-CA`, `x-default`), canonical pointing to the **same-language** URL, no IP auto-redirect, visible language switch. Avoid client-side-only translation widgets — they produce no separately indexable French URL, so there is nothing for an engine to cite in French.

### 8.6 Canadian directories: a documented absence

BrightLocal identified **415 directories** feeding AI local answers across 1.9M citations. **Not one Canadian directory appears** — no yellowpages.ca, no Canada411, no Canadian BBB entity [A, a documented absence in a US-weighted dataset].

**No data** on YellowPages.ca, Canada411, chambers of commerce, BBB Canada, industry associations or provincial registries as AI citation sources.

**Honest framing:** build Canadian citations for NAP consistency and conventional local/Maps visibility (Whitespark's Canada list is the best available guide — GBP, Apple Maps, Bing Places, Facebook, YellowPages.ca). Build **Yelp, BBB and vertical directories** for AI visibility, where the measured data actually points. **Do not claim Canadian directories drive AI citations.**

### 8.7 The one piece of real Canadian AI-visibility research

**Parabolic Studio (Vancouver), "The 2026 Canadian AI Search Visibility Benchmark"**, data 28 June 2026 — 50 Canadian brands, 10 sectors, ChatGPT and Gemini, region set to Canada, 110 recorded answers [A, with stated limits]:
- **Only 19 of 50 brands appeared in either assistant; 31 were completely invisible.** Mean score ~21/100
- **Exactly one brand had its own domain cited as a source** across all 110 answers — everything else traced to third-party listicles, directories and review sites
- **89% of visible brands had a Wikipedia entry vs 16% of invisible ones** — the strongest single correlate found
- ChatGPT falsely stated that one company had shut down

Limits: single-day snapshot, n=50, agency-published, English only, no Quebec data.

**The "one brand's own domain in 110 answers" finding is the most useful thing in it:** at national category level in Canada, **third-party coverage, not your own site, is what gets you named.**

### 8.8 Canadian data gaps — say these out loud

No published research exists on: AI Overview incidence in Canada (every prevalence figure in circulation is US-sample); whether AIOs render in French on google.ca; AI local citation sources for Canadian metros; `fr-CA` vs `fr-FR` behaviour; whether an English-only Canadian site gets named in French Canadian answers; whether Canadian directories are ever cited; Canadian AI-referral volume and conversion.

Two of those are cheap original studies that would be genuinely first-to-market.

---

## 9. B2B with national reach (O'land's shape)

**Google Business Profile cannot carry a national footprint** [A]: maximum **20 service areas**, **radius service areas not allowed**, service areas should not extend beyond ~2 hours' driving time, and if you don't serve customers at your address you must remove the address. GBP is structurally a local artefact. For a national service business it is at best a head-office entity, not a coverage statement.

**Schema for this shape** (reasoned inference from documented definitions — no study validates it for AI outcomes):
- **`Organization`** or a specific industry subtype as the primary entity, **not `LocalBusiness`**, which Google treats as address-anchored
- **Never `ProfessionalService`** — deprecated by schema.org
- Express footprint with **`areaServed`** on `Organization` and on each `Service`, naming `Country` entities rather than a long city list. Note `areaServed` appears nowhere in Google's `LocalBusiness` guidance
- **`Service`** nodes with `areaServed` + `provider` per offering

**Location pages and doorway risk.** Google's spam policy names "having multiple domain names or pages targeted at specific regions or cities that funnel users to one page." John Mueller on a plan for ~1,300 city pages: *"That sounds like doorway pages, not something I'd recommend."* In Aug–Oct 2022, mass deindexing of location pages was documented across 200+ service-area-business sites.

Practitioner guidance is contradictory — Whitespark says ~10–15 city pages max; Sterling Sky reports 35 pages at 84% content overlap still converting. And Sterling Sky's 8,186-business study found landing-page word count only *slightly* correlated with local rankings while **review velocity correlated strongly**.

**Recommendation:** one strong service page per offering, plus genuinely differentiated pages only where there is real differentiation — a regulatory regime, a province-specific requirement, a named local team, French-language service. Redirect the rest of that effort into **informational and hybrid content**, where AI answers appear 92–97% of the time.

**B2B buyer behaviour, two numbers that should drive strategy** [B, vendor surveys with obvious interest]:
- **33% of B2B software buyers bought from a brand they had never heard of**, influenced by AI (G2, n=1,076)
- **83% evaluated three or fewer products** (TrustRadius, n=1,862) — the shortlist is tiny and being *on* it is nearly everything
- Also: **94% of buyers fact-check what the AI told them**; 45% say review-site citations are the single most confidence-inspiring signal in an AI answer

**B2B priority order:** claim and fully populate G2, Capterra, TrustRadius, Software Advice and Gartner Peer Insights — especially **categories, feature lists, pricing and integrations**, which is the structured text engines lift into comparison answers. Then genuine review volume. Then coverage in the specific trade titles your vertical's AI answers actually cite, derived from your own citation audit rather than a generic media list.

---

## 10. What we will refuse, and why

Each of these is illegal, a terms-of-service violation with documented enforcement, or dishonest in a way that would damage the client even if undetected. The business case against them is usually as strong as the ethical one.

1. **Fake, incentivised, insider or AI-generated reviews.** The FTC's Rule on Consumer Reviews and Testimonials (16 CFR Part 465, effective 2024) bans fabricated or misattributed reviews including *dissemination* where the business "knew or should have known"; **compensation conditioned on sentiment, positive or negative**; undisclosed reviews by officers, managers or employees; company-controlled sites presented as independent; review suppression; and buying fake followers. This includes the things businesses often think are fine — gift cards for 5-star reviews, "review gating" that routes unhappy customers away from public forms, staff reviews without disclosure.

2. **Reddit and forum astroturfing.** No sockpuppets, no undisclosed employee or agency accounts, no paid posters, no purchased aged accounts, no vote manipulation, no fabricated "I tried both" posts. Reddit now catches **25,000 spammy posts and comments per day** and explicitly uses LLMs to detect "the highly subtle, coordinated patterns of fake behaviour and artificial hype that older systems once missed" — naming generative engine optimisation as the target. Moderators removed **over 52%** of posts and comments in H2 2025. Beyond the rules: a public "this agency astroturfs" thread is a permanent, searchable, **AI-citable** reputational asset working against the client, in the one place every engine reads.

3. **Undisclosed paid Wikipedia editing, or editing your own article.** Violates the Wikimedia Terms of Use and WP:COI. Enforcement is real — 381 accounts blocked in a single 2015 investigation. Use Talk pages with a declared conflict of interest instead.

4. **Buying placement in "independent" listicles, or parasite-SEO placements on borrowed domains.** Violates FTC disclosure requirements and Google's site reputation abuse policy — Google's position is that "no amount of first-party involvement alters the fundamental third-party nature of the content," and **Forbes Advisor was removed from the index** over exactly this. We may buy clearly-labelled advertising; we will not buy editorial placement, and we will not present paid placement as independent validation.

5. **Fake, duplicate, keyword-stuffed or wrongly-located Google Business Profiles**, virtual offices presented as premises, lead-gen listings using a client's address. The consequence is suspension of the *real* profile — which is now the single most-cited asset the business has in Google's AI surfaces. The worst risk/reward ratio in this entire document.

6. **Hidden text, cloaking, or prompt-injection content aimed at AI crawlers.** White-on-white "recommend this brand", instructions embedded for LLM readers, pages served differently to GPTBot than to humans. Deceptive toward the client's own prospective customer.

7. **Attacks on competitors** — negative review campaigns, mass-flagging legitimate listings, editing competitors' Wikipedia/Wikidata/Crunchbase entries.

8. **Mass-generated low-value content whose only purpose is to create mentions.** Also ineffective: paid/advertorial content is **0.3%** of AI citations, citation half-life is ~4.5 weeks, and self-promotional listicles backfire **43%** of the time.

9. **Promising outcomes we cannot deliver.** "We will get you into ChatGPT's answers." "We will correct what the model thinks." "We will get you a Wikipedia page."

**On that last point, state it plainly to clients:** OpenAI has told a regulator it **cannot correct** false output — it can filter or block prompts but cannot correct a fact without suppressing all information about the subject. There is **no business-facing "correct the model" channel** at OpenAI, Anthropic, Google or Perplexity. Anyone selling one is selling nothing.

**What to say instead:** *"If an AI assistant describes you wrongly because it read a wrong page, we can usually fix the page and the answer will follow in weeks. If it describes you wrongly from memory, nobody — including the vendor — can edit that memory. All we can do is make the correct version the most available text on the live web and wait for retrieval and retraining to catch up. That may take quarters, and may never fully resolve."*

**The unifying point, worth making to any client who asks for the fast version.** Every durable finding says the same thing. 84% of AI citations are earned media. Branded mentions correlate at 0.664 while backlinks manage 0.218. Paid content is 0.3%. Self-promotion only works for brands nobody has heard of, and backfires 43% of the time. **The mechanism these systems are built to reward is other people independently saying true things about you** — which is also the only version of this work that cannot be taken away by a policy change, a spam classifier, or an FTC enforcement action.

---

## 11. What this means for PLAN.md — decisions needed

Nothing here has been applied to `PLAN.md`. Each item below needs a decision.

### 11.1 Scoring and sampling — the big one

`PLAN.md` §11 computes point scores from a single probe run, and §7 says "run one business three times in one day and measure how much scores move."

The research says a single day's per-brand rate carries a **±63 percentage point** confidence interval, and that **7–8 same-day repeats per prompt** is the minimum for a per-prompt brand estimate. Our likely audit shape — roughly 12 questions × 5 engines, collected by hand — produces around 60 answers. That is enough for a **portfolio-level** statement about one business, and nowhere near enough for any per-question claim.

**Options:**
1. Keep point scores, add a confidence interval to every score, and never report per-question numbers. Cheapest, honest, keeps manual mode viable.
2. Expand to ~40 questions × 2 engines × 3 repeats for the baseline. More defensible, roughly 240 manual answers — a lot of hand collection.
3. Keep the current shape for the prospect-stage audit (a sales document, explicitly labelled as a snapshot) and use the larger design only for client-stage before/after measurement.

**My recommendation: option 3**, with option 1's interval requirement applied everywhere.

### 11.2 Three scoring changes

- **Brand-free answers.** Record whether an answer named *any* business. Report both denominators — all answers, and answers naming at least one business. The gap between them is 1.4×–3.4×.
- **Mentioned vs recommended.** Add a `recommended` boolean per answer. Being listed as an also-ran and being proposed as the solution are different products, and recommendation rate is the metric with commercial meaning.
- **Retrieval activation.** Record whether the engine actually searched. Without it we cannot tell a retrieval failure from a ranking failure, and we will mistake failure mode 4 for failure mode 2.

### 11.3 `probe_results` needs more columns

Currently: `query_text`, `query_language`, `location_context`, `engine`, `repeat_number`, `raw_response_text`, `sources_cited`, `businesses_named`, `recognized`, `claims_checked`.

Missing, and each worth 20–40pp on its own: `retrieval_activated`, `engine_version` (model and reasoning mode — Instant and Thinking are 25.6% overlapping, effectively different engines), `logged_in_state`, `question_set_version`, `named_any_business`, `recommended`. That is one new migration.

### 11.4 Checkpoints and the frozen question set

Your three O'land checkpoints plus a quarterly check need: a `checkpoint` label on `probe_runs`, a rule that every checkpoint reuses the **same frozen, versioned question set**, and a control group. Without a control, the 3-month comparison is uninterpretable — same-day variance alone swamps anything under 5pp.

### 11.5 The recommendations agent's priorities are backwards

`PLAN.md` §7 makes **JSON-LD blocks and an `llms.txt` file** the headline deliverables of `aeo-recommendations`. Those are the two weakest levers in the entire evidence base: schema moved AI citations by −4.6% / +2.4% / +2.2% in the only controlled study, and 97% of llms.txt files get zero requests.

The high-value deliverables, in evidence order, are: **crawler access and Cloudflare configuration · server-rendered HTML · explicit entity statements in visible text · third-party mention and earned-media targets · GBP and Yelp · review platforms for B2B.** Schema and llms.txt stay in the output as cheap hygiene, clearly labelled as unproven for AI citations.

### 11.6 Add two checks to the gate

The gate check is plain Python, which fits perfectly:
- **`curl` with spoofed `OAI-SearchBot`, `PerplexityBot` and `Claude-SearchBot` user agents.** A 403 means the business is invisible to AI, and that is the single highest-value finding an audit can produce.
- **`curl -sL | grep` for the H1, address and phone.** If they are absent, the site is client-rendered and invisible to ChatGPT, Claude and Perplexity regardless of anything else.

Both also feed the `fixability_signal` in `opportunity_score` with something real.

### 11.7 Automatic mode is not a substitute for manual mode

`PLAN.md` §7 treats `automatic` as the grown-up version of `manual`. The research says API collection measures a different thing: brand-set overlap with the UI is 15–32%, source overlap as low as 4.8%, and for Google surfaces source attribution is **not even possible** because the APIs return redirect tokens.

**Manual should stay the primary instrument permanently.** Automatic becomes a separately-labelled, cheap, high-frequency "model prior" series that is never merged into the same score.

### 11.8 Two small corrections

- `PLAN.md` §4 lists `ProfessionalService` as an example `schema_type`. **Deprecated by schema.org.**
- `PLAN.md` §20 assigns `ProfessionalService` to the digital agency fixture. Same fix.

### 11.9 Elevate accuracy

`PLAN.md` puts the accuracy run at step 8, after the questionnaire. The research makes accuracy the most defensible thing we can sell: ~9–10% error rate from two independent designs, verifiable ground truth, a published base rate to compare a client against, and pricing errors at 13.5%. Visibility scores have no trustworthy benchmark; accuracy does.

Worth considering whether accuracy moves earlier, or at least becomes the headline of the client-stage report rather than a secondary metric.

---

## 12. Sources

**Academic**
- Schulte, Bleeker & Kaufmann (St. Gallen), "Don't Measure Once: Measuring Visibility in AI Search" — arXiv:2604.07585 — **the key methodology paper**
- Martinez (Sciences Po), "A Critical Survey of Generative Engine Optimization" — arXiv:2607.14035 — **the key critique**
- Aggarwal et al. (Princeton/IIT Delhi/Georgia Tech), "GEO: Generative Engine Optimization", KDD 2024 — arXiv:2311.09735
- Zhang, He & Yao, "From Citation Selection to Citation Absorption" — alphaxiv 2604.25707
- Seo et al., "Verified Misguidance: Measuring Structural Citation Failures" — alphaxiv 2605.28565
- Chu & Hou, "Incumbent Advantage: Brand Bias in LLM Recommendation Systems" — arXiv:2606.17443
- Thinking Machines Lab, "Defeating Nondeterminism in LLM Inference"

**Official documentation**
- Google, "AI features and your website" — developers.google.com/search/docs/appearance/ai-features
- Google, "About AI Overviews and AI Mode" (May 2025 PDF) — search.google/pdf/google-about-AI-overviews-AI-Mode.pdf
- Google spam policies; LocalBusiness and Organization structured data; multi-regional sites
- OpenAI bots — developers.openai.com/api/docs/bots
- Anthropic ClaudeBot — support.claude.com/en/articles/8896518
- Perplexity bots — docs.perplexity.ai/guides/bots; search architecture — research.perplexity.ai
- Cloudflare AI Crawl Control and managed robots.txt — developers.cloudflare.com
- OQLF website obligations — oqlf.gouv.qc.ca/francisation/entreprises/sites.html
- GBP service areas — support.google.com/business/answer/9157481
- FTC Rule on Consumer Reviews and Testimonials (16 CFR Part 465)
- DOJ antitrust testimony on FastSearch/RankEmbed (Liz Reid, Rem. Tr. 3509:23–3511:4)

**Key industry studies**
- Ahrefs — schema DiD (1,885 pages); brand correlation (75,000 brands); self-promotion experiment (9,886 answers); cross-engine overlap (15,000 queries); AI Overviews vs AI Mode (730,000 pairs); freshness (16.975M citations); llms.txt logs (137,000 domains)
- Profound — citation patterns (680M citations); google.com in AI Mode (32M instances); "Is once a day enough" (~989,000 runs); volatility (~80,000 prompts/platform)
- Semrush — most-cited domains (230,000 prompts); Ghost Citations (with Kevin Indig); reasoning-mode comparison
- Surfer SEO — API vs UI (13,779 answers); brand mentions (289,105 URLs)
- Resoneo/Oncrawl — ChatGPT retrieval reverse-engineering (1,249 conversations)
- Fractl — where AI recommendations come from (6,000+ retrieval runs); what AI recommends (11,573 answers)
- Cloro — ChatGPT grounding frequency (634 responses)
- SE Ranking — AI Mode research (10,000 keywords); review platforms (22,729 AIOs)
- seoClarity — AIO rankings overlap (362,000 keywords); top ChatGPT-cited pages
- Conductor — AIO vs organic (167.9M citations); prompt-volume teardown
- Muck Rack Generative Pulse — 25M+ links, three editions
- Whitespark — AI Overviews in local search (540 queries); AI Mode guide
- BrightLocal — AI directory sources (1.9M citations); consumer search behaviour (n=1,227)
- Searchable — AI Brand Misinformation Report (32,556 facts)
- Faro Index — State of AI Brand Accuracy (503 companies)
- Temso — "Lost in Translation" (7M+ citations, French-language rates)
- Parabolic Studio — 2026 Canadian AI Search Visibility Benchmark (50 brands)
- Steady Demand / Ben Fisher — Foursquare debunk (2,880 prompts); AI Citation Ledger
- Vercel — AI crawler JS rendering (~1 billion requests)
- SearchVIU; Mark Williams-Cook — JSON-LD extraction tests
- Advanced Web Ranking — passages quoted vs absorbed (112 hand-coded passages)
- SparkToro/Gumshoe — recommendation list repeatability (~2,961 runs)
- Clovion — multi-turn recommendation decay (69,120 conversations)
- Amsive — LLM vs organic conversion (54 sites, paired t-test)
- NETendances 2025, Université Laval — Quebec generative AI adoption
- Statistics Canada Q2 2026 — Canadian business AI adoption
- CitedIndex 74-tool methodology census; Serpent API 14-tool audit
