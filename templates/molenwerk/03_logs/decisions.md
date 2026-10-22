# Decisions Molenwerk BV

Newest first. A decision without its why will be taken again.

2026-08-15: Park the Koeriersapp. Why: the unit economics review showed a cost of 7.40 euro per
delivery against a customer willingness to pay of at most 4 euro; the gap does not close below
roughly 40 deliveries per day and we average 11. Revisit condition: average daily orders in the
Zwolle zone exceed 35 for a full month. Approved: Femke.

2026-07-22: Payment provider is Mollie, not Stripe. Why: iDEAL is native and first-class at
Mollie, settlement is EUR-only which fits us, and support is Dutch-speaking, which the agency
asked for. Stripe won on API polish but that is not our bottleneck. Approved: Femke.

2026-06-30: One warehouse, Zwolle, no second location this year. Why: a second location saves
at most one day of shipping for 9 percent of orders but adds fixed costs of 3,800 euro per month;
the math only works above 250 orders per day and we are at 90. Approved: Femke.

2026-06-12: Webshop platform is Shopware 6, not staying on the custom 2021 stack. Why: the old
stack has one maintainer (departed), no test suite, and every change breaks checkout. Buying a
maintained platform is cheaper than resurrecting expertise. Approved: Femke.
