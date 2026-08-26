# Overseas regions and territories

France, Portugal, Spain, Denmark and the Netherlands each administer territories outside
continental Europe, and Norway administers one further north. EU primary law splits the
former into two categories, and only one of the two exists in NUTS. A `territory` registry
of 26 entries — the nine outermost regions, the thirteen Annex II OCTs (mapped to eleven ISO
codes, since Bonaire, Saba and Sint Eustatius share `BQ`), and six other European territories
outside the ordinary country set — gates every lookup before the tier cascade runs, so the
answer always matches the territory's real status.

## The two EU categories

| | Outermost regions (OR / RUP) | Overseas countries and territories (OCT) |
|---|---|---|
| **Legal basis** | Art. 349 TFEU | Part Four TFEU + Annex II |
| **Status** | Part of the EU | Associated with the EU, not part of it |
| **EU law** | Applies in full (with adaptations) | Does not apply |
| **In NUTS?** | **Yes**, with one exception (Saint-Martin) | **No** |
| **Count** | 9 | 13 (mapped to 11 ISO 3166-1 codes; all UK OCTs left in 2020) |
| **This service** | Resolves `exact` from TERCET, `nuts_coverage: "full"` | Returns a `territory` block with null NUTS, `nuts_coverage: "none"` |

## Outermost regions

Eight of the nine are ordinary NUTS territory and resolve from the TERCET files like any
metropolitan postal code, carrying `nuts_coverage: "full"`. Saint-Martin is legally an
outermost region but has no NUTS code of its own.

| Territory | ISO | Postal codes | NUTS3 | `nuts_coverage` |
|---|---|---|---|---|
| Guadeloupe (FR) | `GP` | `971xx` | `FRY10` | `full` |
| Martinique (FR) | `MQ` | `972xx` | `FRY20` | `full` |
| French Guiana (FR) | `GF` | `973xx` | `FRY30` | `full` |
| Réunion (FR) | `RE` | `974xx` | `FRY40` | `full` |
| Mayotte (FR) | `YT` | `976xx` | `FRY50` | `full` |
| Saint-Martin (FR) | `MF` | `97150` | — | `none` |
| Azores (PT) | — | `9500-xxx`–`9980-xxx` | `PT200` | `full` |
| Madeira (PT) | — | `9000-xxx`–`9399-xxx` | `PT300` | `full` |
| Canary Islands (ES) | — | `35xxx`, `38xxx` | `ES703`–`ES709` (per island) | `full` |

The five French RUPs sit under `FRY`, a NUTS1 grouping with no metropolitan equivalent. The
Canary Islands resolve at island level (`ES705` Gran Canaria, `ES709` Tenerife, and so on), not
as one region. Azores, Madeira and the Canary Islands have no ISO 3166-1 alpha-2 code of their
own — reach them only through `PT` or `ES` postal codes; the registry keys them as `PT-20`,
`PT-30` and `ES-CN`.

> **Saint-Martin's** single postal code, `97150`, falls inside Guadeloupe's `971xx` block in
> the raw digits, but the territory registry gates on the code itself, not the prefix, and
> `97150` is not a row TERCET carries. A lookup for `MF/97150` or `FR/97150` now returns
> `200` with a `territory` block and null NUTS (`nuts_coverage: "none"`) — not Guadeloupe's
> `FRY10`, which is what it returned before this registry existed.

## Overseas countries and territories

None of the 13 OCTs has a NUTS region, so no lookup can return one. All eleven ISO-coded
entries return `200` with a `territory` block and `nuts_coverage: "none"` — either on their
own ISO code, or on a well-formed postal code under the administering country that falls in
the territory's range.

| Territory | ISO | Postal codes |
|---|---|---|
| Greenland (DK) | `GL` | `39xx` (Danish scheme) |
| French Polynesia (FR) | `PF` | `987xx` |
| New Caledonia (FR) | `NC` | `988xx` |
| Wallis and Futuna (FR) | `WF` | `986xx` |
| Saint-Pierre-et-Miquelon (FR) | `PM` | `975xx` |
| Saint-Barthélemy (FR) | `BL` | `97133`, `977xx` |
| French Southern and Antarctic Lands (FR) | `TF` | `984xx` |
| Aruba (NL) | `AW` | none |
| Curaçao (NL) | `CW` | none |
| Sint Maarten (NL) | `SX` | none |
| Bonaire, Saba, Sint Eustatius (NL) | `BQ` | none |

Saint-Barthélemy is the one exception to "null NUTS": its `97133` code is carried as an actual
row in the GISCO TERCET FR file, left over from before it became an OCT on 1 January 2012. A
lookup for `BL/97133` or `FR/97133` returns `200` with `nuts3: "FRY10"` (Guadeloupe) at full
confidence, flagged `nuts_coverage: "tercet_entry_only"` — Eurostat's own row is honoured, but
labelled so a consumer can tell it apart from a normal NUTS classification. No other OCT code
has a TERCET row, so this behaviour is unique to Saint-Barthélemy.

The four Dutch OCTs use no postal codes at all: `has_postal_system` is false in the registry,
so a lookup on `AW`, `CW`, `SX` or `BQ` needs no `postal_code` and answers directly from the
country code; `GET /pattern` for any of them returns `404`.

## Other European territories

Six further territories sit outside the ordinary country set — associated with an EEA/EFTA
or non-EU state, not with the EU's own treaty categories:

| Territory | ISO | Administering country | `nuts_coverage` |
|---|---|---|---|
| Svalbard and Jan Mayen | `SJ` | Norway (`NO`) | `full` |
| Faroe Islands | `FO` | Denmark (`DK`) | `none` |
| Gibraltar | `GI` | United Kingdom (`UK`) | `none` |
| Jersey | `JE` | United Kingdom (`UK`) | `none` |
| Guernsey | `GG` | United Kingdom (`UK`) | `none` |
| Isle of Man | `IM` | United Kingdom (`UK`) | `none` |

**Svalbard and Jan Mayen** is the one territory in this group that *is* in NUTS: it is
statistically part of Norway's EFTA regions. Postal code `8099` resolves to `NO0B1` (Jan
Mayen); the `917xx` block resolves to `NO0B2` (Svalbard) — for example `9170` and codes up to
`9178`. Both are reachable via `SJ` or via `NO`.

**The Faroe Islands** are an autonomous Danish territory the Treaties do not apply to (Art.
355(5)(a) TFEU). There is no NUTS coverage and no GISCO TERCET file, so `FO` lookups return a
`territory` block with null NUTS and `nuts_coverage: "none"`. Earlier releases of this service
fabricated `FO0` / `FO00` / `FO000` as a synthetic single-region result — that code was
invented by this project, never published by Eurostat, and has been removed. Contrast
Montenegro's `ME000`, which is a genuine Eurostat single-region NUTS code.

**Gibraltar and the three Crown Dependencies** use UK-style postcodes but sit outside ITL
geography and the NSPL, so they cannot resolve even when UK/ITL coverage is configured. A
well-formed UK-pattern code under `GI`, `JE`, `GG` or `IM` returns `200` with a `territory`
block and null NUTS.

## Implemented behaviour

The governing rule: the territory registry is a gate in front of the lookup cascade, not a
tier inside it. It classifies every `(country, postal_code)` pair before Tier 1 runs.

- A territory Eurostat classifies (`in_nuts: true`) — the nine outermost regions bar
  Saint-Martin, plus Svalbard and Jan Mayen — runs the **full five-tier cascade** against the
  administering country's data. `nuts_coverage: "full"`.
- A territory outside NUTS runs **Tier 1 only** — a single exact-match probe against the
  GISCO TERCET row for that country and postal code. Every approximation tier (prefix
  matching, country-level majority vote, single-NUTS3 fallback) is structurally unreachable,
  which is what stops an OCT or Crown Dependency code from being answered with a neighbouring
  European region:
  - A TERCET row exists → `nuts_coverage: "tercet_entry_only"` (Saint-Barthélemy's `97133`
    only).
  - No TERCET row → `nuts_coverage: "none"`: `match_type` and all six NUTS fields are `null`.
    The response is still `200` — the absence of a NUTS code is the answer, not an error.

**Two routes reach the same territory.** A territory is reachable by its own ISO code
(`GL/3900`) or by the administering country's code (`DK/3900`); both return the same body
apart from the echoed `country_code`. The routes are not symmetric, though: on the ISO route,
a postal code that is well-formed for the administering country but falls outside the named
territory's own ranges is rejected with `404` rather than answered from the parent country's
data — `GL/2100` is a valid Danish code for Copenhagen, but it is not Greenlandic, so it 404s
rather than silently returning a Copenhagen NUTS region under a Greenland lookup.

The whole-country territories — `FO`, `GI`, `JE`, `GG` and `IM` — are the exception to "two
routes": they have no postal prefix of their own carved out of the administering country's
scheme, so only their own ISO code resolves. `JE/JE2 3XP` returns `200`; `UK/JE2 3XP` returns
`404`.

The `/resolve` geocoding fallback does not correct a `nuts_coverage: "none"` result either:
no NUTS polygon covers these territories, so point-in-polygon has nothing to resolve against.
The geocoder is skipped entirely — `geocode.status: "not_attempted"`, `resolved_via: "none"`
— rather than spending a Photon call on an address that can never map to a NUTS-3 region.

If you process data that may contain territorial addresses, you no longer need to filter them
out before lookup: querying them returns a clearly labelled `200` with `nuts_coverage`, not a
misleading European region and not a bare `404`.

## Why the asymmetry exists

Territories move between the two EU categories by European Council decision, and NUTS follows
the legal status rather than geography. Saint-Barthélemy was an outermost region until
1 January 2012, when it became an OCT; Mayotte moved the other way in 2014. Neighbouring
islands can therefore sit on opposite sides of the line — New Caledonia and French Polynesia
have no NUTS code at all, while Réunion and Mayotte get full NUTS3 coverage.
