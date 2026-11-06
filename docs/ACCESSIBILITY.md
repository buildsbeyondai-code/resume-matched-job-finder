# Accessibility

**Target:** WCAG 2.2 AA, UEUIS Enterprise level.

## What is built in
- Skip link, header/nav/main landmarks, `lang`, one h1 per view, and tab links with `aria-current`.
- Every input has a visible `<label>`. Errors are given as text, set `aria-invalid`, and the profile form has an error summary that links to the fields. User input is kept on error.
- Live region (`#announcer`) announces search start and completion, failures, saves, hides and undo. Failure banners use `role="alert"`.
- The match score is shown as a number plus a text band ("Strong/Partial/Weak match") with an aria-label, so it never relies on colour alone.
- Focus ring is 3 px using the `--focus` token (≥ 3:1 in both themes). Buttons are at least 36 px tall and link-like buttons at least 24 px.
- Reflow: fluid grids, a 720 px breakpoint, tables collapse, long text wraps, and there are no fixed widths of 100 px or more.
- Reduced motion turns off spinners. Forced-colours mode uses system colours.
- **Job portals (2026-11-06):**
  - The Naukri and LinkedIn toggles are native checkboxes inside a `<fieldset>`/`<legend>`, with wrapping `<label>`s (targets ≥ 24 px).
  - The pause input has a label, the unit "seconds (5 to 600)" linked by `aria-describedby`, `min`/`max`, and an inline `role="alert"` error that is also linked by `aria-describedby`. `aria-invalid` is set, focus moves to the field, and the typed value is kept.
  - The run progress (`#appProgress`) is a polite live region. It changes only when the step changes. The per-second countdown sits next to it and is `aria-hidden`, so screen readers are not flooded.
  - The "via …" channel is shown as text (a chip), not colour alone.
  - "Open in browser" is a link (it navigates), and its accessible name includes the job and "opens in a new tab". Retry, I applied, Cancel auto apply and the sign-in buttons are buttons (they act).
  - No new colour tokens were added. New chips reuse existing token pairs that the contrast tests already check in both themes.

- **Unattended auto-apply (2026-11-06):**
  - The status bar and kill switch are on every view. The switch is a real `<button>` named "Pause auto-apply" or "Resume auto-apply", at least 24 px tall, and keyboard reachable from the top of every page. Pausing is announced assertively (`#uaAlert`), and progress politely (`#announcer`). Resume opens a modal `<dialog>` that asks for confirmation, and focus returns to the switch when it closes.
  - The settings form has visible labels, units linked by `aria-describedby`, an error summary that receives focus and links to each field, inline errors, and `aria-invalid`. Input is kept on error. While the summary is being reviewed, the fields are disabled.
  - The summary and run log use real `<table>`s with `<th>` header cells. Outcomes are shown as text plus an icon (✓ Submitted, ! Needs you, – Skipped). Proof and "Open posting" links have descriptive names that include the job, the company and "opens in a new tab". Run-log rows expand with `aria-expanded` buttons.
  - The Unattended, Today and Run log sub-views use `aria-current="page"` in the Applications sub-navigation. The explanatory text uses `.desc`, which the Descriptions switch hides. Hints and errors don't use it.

## How it was tested
- `tests/test_ui_static.py` (automated): WCAG contrast ratios of every text and UI token pair in **both** themes (≥ 4.5:1 text, ≥ 3:1 UI); system-dark tokens equal manual-dark tokens; labels for all controls; button types; landmarks and skip link; no innerHTML; no gradients, glass or text-shadow; forced-colours and reduced-motion rules present; one primary button per view.
- `tests/test_ui_browser.py` (automated, real Chromium, added 2026-11-06):
  - Keyboard only: Tab from the top reaches the portal checkboxes, the pause, Save and the sign-in buttons in a logical order. Space toggles a checkbox and Enter submits.
  - The focus outline is ≥ 2 px.
  - Every visible control on all five tabs has an accessible name.
  - The portal labels and buttons are ≥ 24 px tall.
  - There is no horizontal scroll at 320 px on all five tabs.
  - Light and dark themes differ in the header, navigation and page background, and the theme choice is remembered after a reload.
  - The inline error and `aria-invalid` work, and nothing is saved while the pause is invalid.
  - The live region has `aria-live="polite"`.
  - There are no script errors, and no request leaves the app.

- `tests/test_unattended_ui.py` (automated, real Chromium, 2026-11-06):
  - The kill switch on all 8 views: its name, a height of at least 24 px, Tab reachability, a focus outline of at least 2 px, Enter pausing in under 5 s, the assertive live region, and the Resume confirmation dialog.
  - The form error summary with focus links, `aria-invalid`, kept input, and nothing saved.
  - Loading, empty, error with Retry, and success states of every new view.
  - Accessible names on every visible control.
  - No horizontal scroll at 320 px in both the light and the dark theme.
  - The status bar differs between the themes.
  - The Descriptions switch hides only the explanatory text.
  - No script errors and no external requests.

## Known issues / not verified
- **Not run:** an axe-core scan (not installed), a screen-reader pass (NVDA/VoiceOver), 200% zoom and text-spacing checks, and checks in Firefox and Safari. These remain UNVERIFIED.
- Remove source uses the native `confirm()` dialog.
- The real Naukri and LinkedIn pages are third-party UIs in the app's browser window and are outside this app's accessibility control.
