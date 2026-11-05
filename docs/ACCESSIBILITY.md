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

## How it was tested
- `tests/test_ui_static.py` (automated): WCAG contrast ratios of every text and UI token pair in **both** themes (≥ 4.5:1 text, ≥ 3:1 UI); system-dark tokens equal manual-dark tokens; labels for all controls; button types; landmarks and skip link; no innerHTML; no gradients, glass or text-shadow; forced-colours and reduced-motion rules present; one primary button per view.

## Known issues / not verified
- **No real-browser testing.** Chromium, Playwright and axe-core could not be installed in the build environment. These are therefore UNVERIFIED: the axe scan, keyboard walk-through, no horizontal scroll at 320 px, 200% zoom, and screen-reader output.
- Remove source uses the native `confirm()` dialog.
- Focus after hiding a job moves to the next job's title, or to Undo if there is none. This has not been checked in a browser.
