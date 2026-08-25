"""What an identifier looks like in a URL.

One rule, two callers. `network.endpoint_pattern` has used it since V3 to
collapse thirty calls to `/users/<uuid>` into one endpoint; `H12`'s
`util.route_template` uses the same rule to collapse twenty renderings of one
screen into one screen.

It lives in its own module because those two would otherwise import each other
— `util` is the lower layer and `network` already depends on it. A second
regex in the other file was the obvious alternative and the wrong one: two
ideas of what an id looks like drift, and the drift is silent, which is exactly
how `G7`'s ledger came to disagree with `H6`'s subdomain policy.

The shapes, deliberately narrow:

* all digits — `/orders/1042`
* 8+ hex characters — a short hash or an id fragment
* 16+ hex-and-hyphen characters — a uuid

Narrow because over-collapsing is the worse failure. `/settings/general` and
`/settings/billing` are different screens, and a rule loose enough to call
`general` an identifier would report a product as having fewer screens than it
has — the same class of mistake as an over-eager redactor scrubbing every label.
"""

from __future__ import annotations

import re

ID_SEGMENT = re.compile(r"^(\d+|[0-9a-f]{8,}|[0-9a-fA-F-]{16,})$")
