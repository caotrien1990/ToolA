---
name: image_mode
description: |
  Use when rendering data as image (PNG/SVG) is more useful than text.
  Common cases: large hint cards, mermaid diagrams, DOM snapshots compressed, table
  visualizations. Saves tokens for CA reading; trades against User device bandwidth.
trigger:
  - task_pattern: "popup hint | render diagram | image of | screenshot | compress this for token"
  - tool: "screenshot_page | render_mermaid (planned)"
preconditions:
  - "T4 stack installed (playwright + pillow per V15 R8 B.0)"
related_skills:
  - 18_runtime_dom_T4
  - 04_tool_tiers
risk_level: low
---

# 19 — Image mode (token-saving via render)

## When image > text

| Content | Text size | Image size | Image win |
|---------|-----------|-----------|-----------|
| 30-row table | ~3 KB | ~10 KB PNG | ❌ text wins for CA reading |
| 100-row table | ~12 KB | ~25 KB PNG | depends on CA model — text |
| Mermaid diagram | ~5 KB markdown + render burden | ~20 KB PNG | ✅ image (rendered server-side) |
| Stack trace 200 lines | ~10 KB | ~80 KB | ❌ text |
| Screenshot UI bug | N/A | ~150 KB | ✅ only path |
| Compressed hint popup (badge/icon) | ~2 KB | ~8 KB | ❌ unless visual UX |

Rule of thumb: **text wins for CA token cost**, **image wins for User visual UX**. Reserve image mode for cases where User benefits (visual diagrams, screenshots) — not for compressing CA-side text.

## Multimodal model caveat

Some User research suggests image is ~30% cheaper than equivalent text. This is **model-specific**:
- Claude/GPT vision: image ~1.5-2 tokens per pixel × resize
- Text: 1 token per ~4 chars
- Net win only when image is small AND content text-heavy
- For CA-to-CA / CA-to-API: text always wins (no vision tokens)

## Tools

- `screenshot_page(page_id, full_page=False)` — Playwright PNG → save → return path + dims
- (Planned R+1) `render_mermaid(diagram_source) → PNG path` — server-side mermaid CLI
- (Planned R+1) `render_table_as_image(rows, cols) → PNG` — small data table viz

## Typical workflow

```
1. CA writes diagram in mermaid text
2. tool render_mermaid(text) → /cache/diagrams/<hash>.png
3. UI displays image; CA can re-read the source text if needed (not the image)
4. Image stored, dedup by content hash
```

## Anti-patterns

- ❌ Encoding text as image to "save tokens for CA" — vision tokens cost more
- ❌ Auto-converting all hints to images — UI bloat + User device bandwidth
- ❌ Screenshot for content that has DOM accessibility tree (use `browser_snapshot` text YAML instead)
- ❌ Generating images for diff/code (text far better)

## When to use (positive cases)

- UI bug reproduction → screenshot_page for visual evidence
- Architecture diagram → render_mermaid for User's mental model
- Skill loop test pass-rate dashboard → render_table_as_image for at-a-glance status
- Visual diff before/after → both screenshots side-by-side

## Guardrails

- Image generation is T4a (read-only) — no trigger needed
- Storage: `.orchestrator_cache/images/` with hash-named files; manual cleanup or TTL policy
- Never embed raw token / PII in image content
- Image alt-text required for accessibility

## Operational Impact (counter-intuitive)

In most CA workflows, **image mode does NOT save tokens** because:
- Vision tokens > text tokens for equivalent semantic content
- Image generation has compute cost on server
- Re-reading image requires vision model invocation (often more expensive)

Image mode wins when:
- User needs visual UI (chart, screenshot, diagram)
- Source text would be > 50 KB (rare in CA workflows)
- Cross-skill image cache reuses same render

## Harness

- Test 1: screenshot_page on test page → returns valid PNG path
- Test 2: render_mermaid (when impl) → produces correct diagram
- Test 3: token comparison: 100-row table as text vs image → text wins
- Test 4: UI bug screenshot → embedded in test report → User opens, sees bug
