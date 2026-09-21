"""Records how a routing decision was reached, for the call detail view.

RoutingEngine used to decide and discard its reasoning, so "why did this call go
there" could only be answered by reading the code and the data by hand. A
RouteTrace is passed through call_data, collects what each guardrail and each
destination did, and is stored on the call.

Deliberately passive: it records what it is told and never influences a
decision. If nothing passes a trace, the engine behaves exactly as before.
"""


class RouteTrace:
    """Collects the steps of one routing decision."""

    def __init__(self):
        self.steps = []
        self.destinations = []
        self._total_considered = 0

    # ── recording ────────────────────────────────────────────────────────────

    def step(self, name: str, passed: bool, detail: str = ''):
        """A guardrail outcome — blacklist, duplicate, caps, balance."""
        self.steps.append({
            'step': name,
            'passed': passed,
            'detail': detail,
        })

    def record_destination(self, rule, destination, eligible: bool, reason: str = ''):
        self.destinations.append({
            'rule_id': str(rule.id),
            'rule_name': rule.name,
            'destination_id': str(destination.id),
            'name': destination.destination,
            'buyer': destination.buyer.name if destination.buyer_id else None,
            'priority': destination.priority,
            'weight': destination.weight,
            'eligible': eligible,
            'reason': reason or None,
        })

    def count_considered(self, n: int):
        """Total destinations in scope, including ones never reached.

        get_valid_destination stops at the first success, so destinations after
        it are neither eligible nor rejected — they were simply not needed. The
        total is recorded separately so the summary is honest about that.
        """
        self._total_considered += n

    # ── output ───────────────────────────────────────────────────────────────

    def as_dict(self, selected=None) -> dict:
        eligible = [d for d in self.destinations if d['eligible']]
        rejected = [d for d in self.destinations if not d['eligible']]
        evaluated = len(self.destinations)

        return {
            'summary': {
                'total_destinations': self._total_considered,
                'evaluated': evaluated,
                'eligible': len(eligible),
                'rejected': len(rejected),
                # Destinations after the winner were never examined
                'not_reached': max(self._total_considered - evaluated, 0),
            },
            'steps': self.steps,
            'eligible_destinations': eligible,
            'rejected_destinations': rejected,
            'filtering_breakdown': self._breakdown(rejected),
            'selected': selected,
        }

    @staticmethod
    def _breakdown(rejected) -> list:
        """Rejection reasons grouped by cause, commonest first."""
        counts = {}
        for d in rejected:
            key = d['reason'] or 'unspecified'
            counts[key] = counts.get(key, 0) + 1
        return [
            {'reason': reason, 'count': n}
            for reason, n in sorted(counts.items(), key=lambda kv: -kv[1])
        ]
