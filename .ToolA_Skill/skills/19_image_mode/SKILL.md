---
name: image_mode
description: |
  Use when rendering data as image (PNG/SVG) is more useful than text.
  Common cases: UI bug evidence, desktop or browser state, visual before/after diffs.
  Saves tokens for CA reading; trades against User device bandwidth. There is no
  renderer tool, so a mermaid diagram or a table stays source text.
trigger:
  - task_pattern: "popup hint | render diagram | image of | screenshot | compress this for token"
  - tool: "screenshot_page | desktop_screenshot"
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
| Mermaid diagram | ~5 KB markdown | no renderer exists (measured 2026-09-17) | ❌ text — source is the only form |
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

- `screenshot_page(page_id, full_page=False)` — Playwright PNG → save → return path + dims (T4)
- `desktop_screenshot(...)` — full-screen PNG to a disk path, never inlined (T4)
- `describe_page(...)` — the DOM as text (T1); the cheap first choice before any pixel

**There is no server-side render tool, and none is scheduled.** Until 2026-09-17 this
section advertised `render_mermaid` and `render_table_as_image` as "(Planned R+1)". Neither
was ever implemented: measured against the live tool surface — the same set
`_tool_names_payload` builds and `/api/tools/names` publishes — both resolve to nothing. A
skill that names a callable which does not exist costs CA a real round trip to discover
that, so the two names are gone from every actionable line here, and the frontmatter
`trigger.tool` is pinned by `tests/test_a31_skill_surface_gates.py`: a token there must
name a live tool, with no `(planned)` escape hatch left to re-add one through.

## Typical workflow

```
1. CA writes the diagram as mermaid source text — no tool call, there is no renderer
2. The source ships in the answer or a `.md` file; User's markdown client renders it
3. CA re-reads that source text when it needs the diagram again, never a picture of it
4. Reach for pixels only when the subject IS pixels — then screenshot_page
```

## Anti-patterns

- ❌ Encoding text as image to "save tokens for CA" — vision tokens cost more
- ❌ Auto-converting all hints to images — UI bloat + User device bandwidth
- ❌ Screenshot for content that has a DOM accessibility tree (use `describe_page` text instead)
- ❌ Generating images for diff/code (text far better)
- ❌ Calling a renderer by name — none exists; write mermaid source instead

## When to use (positive cases)

- UI bug reproduction → screenshot_page for visual evidence
- Architecture diagram → mermaid source text, rendered by User's markdown client
- Skill loop test pass-rate dashboard → a markdown table; text wins at this size
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
- Test 2: every `trigger.tool` token here names a live tool — `tests/test_a31_skill_surface_gates.py`
- Test 3: token comparison: 100-row table as text vs image → text wins
- Test 4: UI bug screenshot → embedded in test report → User opens, sees bug
