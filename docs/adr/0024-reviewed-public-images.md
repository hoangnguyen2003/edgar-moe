# ADR 0024: Allow a declared set of reviewed images in the public bundle

- Status: accepted
- Date: 2026-09-23
- Deciders: project maintainer

## Context

Every file in the publishable bundle is validated as reviewable UTF-8 text, so
no binary blob can carry unreviewed content onto the public surface. That rule
also blocked ordinary web assets: the site had no favicon, no touch icon, and no
link-preview image, so browser tabs showed a default icon and a shared link
rendered as bare text. Browser tabs and link previews are part of the public
boundary, and the project is meant to be shared.

## Decision

- Keep text-only as the default. A file that is not valid UTF-8 is still
  rejected unless it is named in the allowlist.
- Allow exactly `apple-touch-icon.png` and `social-card.png`. Each must:
  - carry the PNG signature and be at most 200,000 bytes;
  - contain only pixel and rendering chunks (`IHDR`, `PLTE`, `tRNS`, `IDAT`,
    `IEND`, `sRGB`, `gAMA`, `cHRM`, `pHYs`), which excludes `tEXt`, `iTXt`,
    `zTXt`, `eXIf`, and `iCCP`;
  - have a valid CRC on every chunk and nothing after `IEND`.
- Keep icons in SVG where possible (`favicon.svg`), because SVG is text and
  needs no exception.
- The social image must respect the current publication boundary. Under
  ADR 0034 it describes the synthetic demo, not withdrawn research figures.
- Follow-up on 2026-09-30: PNG chunk validation was insufficient to enforce
  visual-content review. Bind both the rendered image and its text source to
  reviewed SHA-256 digests in the bundle validator. Check declared images as
  images even when their bytes decode as UTF-8. Changed pixels or source require
  explicit review and digest updates; the gate does not itself establish rights.

## Alternatives considered

- **An SVG social image.** Link-preview services do not render SVG, so shared
  links would still have no image.
- **No images at all.** Keeps the rule simplest, but leaves a default browser
  icon and a bare link preview on a project meant to be shared.
- **Allowing any image type or size.** Restores an unreviewable binary channel,
  which is what the original rule exists to prevent.

## Consequences

Adding a further image is a reviewed change to the allowlist, not a silent
addition. Images must be generated without metadata, since a text or colour
profile chunk fails the gate; a screenshot pipeline that adds metadata must strip
it. The rule bounds size, so a large asset cannot bloat the CDN bundle
unnoticed.

## Verification

Unit tests cover the accepted case and five rejections: a text chunk, bytes
appended after `IEND`, a corrupt chunk checksum, an oversized file, and a
non-PNG file. An undeclared binary elsewhere in the bundle is still rejected. CI
runs the bundle validator on every pull request, and the Vercel build runs it
before publishing. Follow-up regression tests reject an altered pixel-only
image, source/image drift, and text masquerading as a declared image. Visual
inspection of the reviewed PNG remains necessary; a digest alone cannot infer
whether content is appropriate.
