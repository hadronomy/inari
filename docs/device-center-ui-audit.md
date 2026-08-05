# Device Center UI audit

Date: 2026-08-03

## Outcome

Device Center now uses one compact operations shell. The setup assistant stays
separate because it blocks device access until enrollment is complete.

The post-install error came from a doubled slash in generated agent request
paths. Device Center now normalizes the agent endpoint before it creates the
generated client. Network failures also report an unavailable agent instead of
an invalid response.

## Product rules

- Keep the agent service separate from Device Center.
- Open the operations shell when the agent state is unknown.
- Keep Support available during an agent failure.
- Do not restart the service without a person selecting the action.
- Show one relevant recovery action.
- Keep exact diagnostic text inside Support details.
- Use local time in Activity.
- Use an icon and text for each status.
- Keep all controls available from the keyboard.

## Visual system

- Typeface: Atkinson Hyperlegible Next Regular and SemiBold.
- Smallest text: 12 px at the default root size.
- Layout spacing: 4, 8, 12, 16, and 24 px.
- Default window: 1120 × 760 px.
- Minimum window: 780 × 560 px.
- Surfaces: warm neutral canvas with sparse tonal elevation.
- Accent: vermilion for the main action and the Inari mark.
- Status colors: separate success, warning, information, and danger tokens.
- Icons: pinned Lucide SVG files with the bare Inari mark for identity.

## Views

| View | Main purpose | Empty or degraded state |
| --- | --- | --- |
| Overview | Show issues before totals. | Explain that device and job data are unavailable. |
| Devices | Search and inspect hardware. | Separate no devices from no search results. |
| Activity | Review jobs and device events. | State that no activity is recorded. |
| Support | Check service health and recover. | Show the relevant service action and exact diagnostics. |
| Setup | Review trust and choose devices. | Explain the agent failure and offer Try again. |

## Verification

Automated contrast tests check both themes. Normal text and semantic messages
meet WCAG AA. Input boundaries meet the 3:1 non-text contrast target.

Client tests cover endpoint normalization and network error classification.
Device Center tests cover degraded routing, recovery text, device search, and
the contrast tokens.

The release check must inspect every view in both themes at the default and
minimum window sizes. It must also inspect setup trust review, device selection,
empty results, long diagnostics, keyboard focus, and platform scaling.
