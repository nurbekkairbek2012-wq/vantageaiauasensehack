# Vantage AI — finding illegal dumps from space and pricing them in tenge

**An eight-week pilot for city administrations in Kazakhstan**

Team Vantage AI · 1st place, Future Minds Hackathon 2026 · [hakathon-lyart.vercel.app](https://hakathon-lyart.vercel.app/)

---

## In short

We find illegal waste dumps in eight years of free satellite imagery,
estimate the cost of each one in tenge, and turn the result into a field
queue sorted by money. An inspector gets a list — where to go first, what
it costs the budget, what can be recovered as recyclables — and a draft
enforcement act for every site.

Sentinel and Landsat imagery is free and open. The pilot requires no
servers, no procurement and no integration with internal systems.

## What is already done

Northern industrial belt of Astana, a 20 × 20 km square, 2018–2026 archive:

| | |
|---|---|
| Places where vegetation disappeared and did not come back | **385** |
| Left after automatic context filtering (quarries, construction, water, housing) | **59** |
| Reviewed by a person on 0.4–0.8 m imagery — all 59 | **15** in the field queue |
| — identified as dumps | **7** |
| — unclear on imagery, need a site visit | **8** |
| Waste on the identified dumps, estimate | **2,500 t** |
| Cost of removal | **38.0 million ₸** |
| Recoverable as recyclables if sorted on site | **19.3 million ₸** — 51% of the removal cost |
| Saving of the right decision (sorting vs. plain removal) | **7.6 million ₸** |
| Matches with the 27 sites in open registries | **none** |

No site visits have been made yet: all confirmations come from
high-resolution imagery. A photo-documented visit to the fifteen sites is
the first step of the pilot, and the system accepts a visit only with a
photo whose GPS coordinates are next to the site.

## What makes it different

- **Physics, not a black box.** Five independent signals (vegetation
  loss, bare soil, polymer response in SWIR, radar instability, thermal
  anomaly) form an evidence chain; a person makes the decision.
- **Money, not probabilities.** Monte-Carlo estimates with P10–P90
  ranges; every assumption is labelled as sourced, derived or an
  engineering estimate.
- **Measured limits.** We know where the method fails (farmland) and can
  check an area before running it. Four negative results of our own models
  are published with numbers.

- **Complements state monitoring, does not replace it.** Every object is
  checked against the open state waste-monitoring map (KazEOSat-1): of our
  seven confirmed dumps, one is on it and six are not. The same map shows
  183 dumps in our areas, and our archive search finds 18 of them — it
  sees where vegetation disappeared, while waste scattered over grass
  barely changes vegetation in a 10 m pixel and stays invisible.
  A second method, scanning very-high-resolution imagery in windows, finds
  3–4 times more in a first test and is in development.

## The pilot

| Weeks | What happens | Result |
|---|---|---|
| 1–2 | Checking the territory before any processing | "the method will work here" or "it will not" — with a number |
| 3–4 | Full run over the 2018–2026 archive | candidate queue sorted by money |
| 5–6 | Review and photo-documented site visits | confirmed list and a measured removal cost |
| 7–8 | Scheduled monthly run, report | four success metrics |

Success is measured in numbers: more than half of visits find a real
dump; more than half of raw detections are removed without a visit; at
least one site not present in any registry is found; one detection costs
less than one random visit.

## What we are looking for

- **City administrations** ready to run a pilot on one district.
- **Grant and accelerator support** to cover field verification and a
  monthly monitoring service for regional centres.
- **Data partners**: removal tariffs and registries of known sites.

## Contacts

[Name, role] · [phone] · [e-mail]

Live product: https://hakathon-lyart.vercel.app/ ·
Source code: https://github.com/k41270075-stack/hakathon
