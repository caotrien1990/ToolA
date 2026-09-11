# DO NOT put AGENT_ORDER / _COUNCIL_DASHBOARD_AGENT_ORDER here.
# Edit orchestrator_server.py _COUNCIL_DASHBOARD_AGENT_ORDER.
# This module only renders srv._council_dashboard_agents().

def _server_module():
    """Resolve the LIVE orchestrator_server module.

    The daemon runs ``python orchestrator_server.py`` so that module lives in
    sys.modules as ``__main__``; a fresh ``import orchestrator_server`` there
    would execute the whole server a SECOND time (and trip the registry's
    duplicate-handler gate). Always reuse the already-loaded instance.
    """
    import sys
    srv = sys.modules.get("orchestrator_server")
    if srv is not None and hasattr(srv, "_council_dashboard_agents"):
        return srv
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "_council_dashboard_agents"):
        return main
    import orchestrator_server as srv_imported
    return srv_imported


def render_council_dashboard() -> str:
    srv = _server_module()

    esc = __import__('html').escape
    cards = []
    for agent in srv._council_dashboard_agents():
        safe_agent = esc(agent)
        safe_label = esc(srv._COUNCIL_AGENT_LABELS.get(agent, agent))
        eff = srv._council_effective_model(agent)
        eff_prov = esc(eff.get("provider", ""))
        eff_model = esc(eff.get("model", ""))
        init_state = esc(srv._council_runtime_status(agent).get("state") or "idle")
        _valid_models = srv._council_valid_models(agent) or []
        _opts = ''.join(
            f'<option value="{esc(m)}"{" selected" if m == eff.get("model") else ""}>{esc(m)}</option>'
            for m in _valid_models)
        model_dropdown = (
            f'<select class="worker-model-dropdown" data-agent="{safe_agent}" title="Select model (ID_MODELS)" aria-label="Select model for {safe_label}">'
            f'<option value="">— select from ID_MODELS —</option>{_opts}</select>'
        ) if _valid_models else ''
        cards.append(f"""
        <section class="agent-card" data-agent="{safe_agent}">
          <h2>
            <span class="agent-title">
              <span class="agent-name">{safe_label}</span>
              <span class="agent-runtime-status {init_state}" data-field="runtime_status">{init_state}</span>
              <button class="copy-button icon-copy" data-copy-field="latest_res" aria-label="Copy {safe_label} response" title="Copy response">
                <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>
              </button>
            </span>
            <button class="agent-pause-toggle" data-agent="{safe_agent}" data-action="stop" aria-label="Stop {safe_label}" title="Stop {safe_label}" disabled>
              <svg class="icon stop-icon" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="1.5"/></svg>
            </button>
          </h2>
          <div class="model-field">
            <span class="model-field-label">Model</span>
            {model_dropdown}
          </div>
          <div data-field="metrics"></div>
          <div class="worker-model-row" title="Manual provider/model override">
            <input class="worker-provider" data-agent="{safe_agent}" value="{eff_prov}" placeholder="provider" autocomplete="off" spellcheck="false" style="width:100%;" aria-label="Provider for {safe_label}" />
            <input class="worker-model" data-agent="{safe_agent}" value="{eff_model}" placeholder="model" autocomplete="off" spellcheck="false" style="width:100%;" aria-label="Model id for {safe_label}" />
            <button type="button" class="worker-model-set" data-agent="{safe_agent}" title="Set provider/model for {safe_label}">Set</button>
          </div>
          <h3>
            <span>Response</span>
          </h3>
          <pre data-field="latest_res" data-scroll-chain="" class="muted">(loading)</pre>
          <details class="reasoning-box">
            <summary>Reasoning</summary>
            <pre data-field="reasoning" data-scroll-chain="" class="muted reasoning-pre">(none)</pre>
          </details>
          <details class="reasoning-box live-raw-box">
            <summary>Live raw <button type="button" class="live-raw-clear" data-agent="{safe_agent}" title="Clear live raw">clear</button></summary>
            <pre data-field="live_raw" data-scroll-chain="" class="muted reasoning-pre live-raw-pre">(none)</pre>
          </details>
        </section>""")

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Council Dashboard</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #08090a;
      --panel: #0f1011;
      --panel-soft: #141516;
      --control: #141516;
      --elevated: #191a1b;
      --border: rgba(255,255,255,0.08);
      --border-soft: rgba(255,255,255,0.05);
      --text: #f7f8f8;
      --muted: #8a8f98;
      --faint: #62666d;
      --accent: #7170ff;
      --accent-hover: #828fff;
      --accent-soft: rgba(113,112,255,.14);
      --accent-ring: rgba(113,112,255,.32);
      --accent-text: #f7f8f8;
      --ok: #27a644;
      --run: #7170ff;
      --warn: #D29922;
      --err: #F85149;
      --t-fast: 120ms ease;
      --t-base: 200ms cubic-bezier(.4,0,.2,1);
      --t-slow: 320ms cubic-bezier(.4,0,.2,1);
      --narr-w: 360px;
      --control-h: 32px;
      --radius-ctrl: 6px;
      --radius-card: 10px;
      --font: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      --mono: "JetBrains Mono", ui-monospace, "Cascadia Code", Consolas, monospace;
      --chevron: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%238a8f98' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
    }}
    * {{ box-sizing: border-box; }}
    html {{ overflow-x: hidden; max-width: 100%; scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: var(--font);
      line-height: 1.45;
      background: var(--bg);
      color: var(--text);
      overflow-x: hidden;
      max-width: 100%;
      padding-right: 0;
    }}
    @media (max-width: 900px) {{
      :root {{ --narr-w: min(300px, 42vw); }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      html {{ scroll-behavior: auto; }}
      *, *::before, *::after {{
        animation-duration: 0.01ms !important;
        animation-iteration-count: 1 !important;
        transition-duration: 0.01ms !important;
        scroll-behavior: auto !important;
      }}
    }}
    header {{
      position: sticky; top: 0; padding: 12px 18px;
      border-bottom: 1px solid var(--border-soft); background: rgba(8,9,10,.92);
      backdrop-filter: blur(10px); z-index: 3;
    }}
    h1 {{ margin: 0; font-size: 15px; font-weight: 600; color: var(--text); letter-spacing: -0.01em; }}
    .meta {{ color: var(--muted); font-size: 11px; margin-top: 4px; }}
    .header-row {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
    .clear-session-button {{
      background: transparent; color: var(--muted); border: 1px solid var(--border);
      border-radius: 6px; padding: 6px 9px; cursor: pointer; font: 11px/1.2 inherit;
    }}
    .clear-session-button:hover {{ color: var(--text); background: var(--control); }}
    .clear-session-button:disabled {{ opacity: 0.45; cursor: wait; }}
    .icon {{ width: 14px; height: 14px; flex-shrink: 0; }}
    .broker {{
      position: sticky; top: 64px; z-index: 1;
      margin: 12px 14px 0; padding: 16px 18px;
      border: 1px solid var(--border); border-radius: 12px; background: rgba(255,255,255,0.02);
      transition: box-shadow var(--t-base);
      display: flex; flex-direction: column;
      max-height: 46vh; overflow: hidden;
      box-shadow: 0 0 0 1px rgba(255,255,255,0.02);
    }}
    .broker-resize {{
      position: absolute; left: 0; right: 0; bottom: -10px;
      height: 18px; cursor: ns-resize; background: transparent; z-index: 3;
      display: flex; align-items: center; justify-content: center;
    }}
    .broker-resize::before {{
      content: ''; width: 80px; height: 5px;
      background: var(--border); border-radius: 2px; opacity: 0.5;
      transition: opacity var(--t-fast), background var(--t-fast);
    }}
    .broker-resize:hover::before {{ opacity: 1; background: var(--accent); }}
    .broker:focus-within {{ box-shadow: 0 0 0 2px var(--accent-ring); }}
    .broker-row {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }}
    .broker select, .broker textarea {{
      background: var(--control); color: var(--text); border: 1px solid var(--border); border-radius: 10px;
      padding: 8px 10px; font: 12px/1.3 inherit;
    }}
    .broker select {{
      appearance: none; -webkit-appearance: none;
      min-height: var(--control-h); padding-right: 28px;
      background-color: var(--elevated);
      background-image: var(--chevron);
      background-repeat: no-repeat;
      background-position: right 10px center;
      background-size: 12px;
      font-family: var(--mono); font-size: 11px; font-weight: 500;
      color-scheme: dark;
    }}
    .broker select:focus {{ outline: none; border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-ring); }}
    .broker textarea {{
      flex: 1 1 520px; min-width: 260px; min-height: 74px; max-height: 180px;
      line-height: 1.45; white-space: pre-wrap;
      resize: vertical;
    }}
    .broker button {{
      background: var(--control); color: var(--text); border: none; border-radius: 999px;
      padding: 8px 12px; cursor: pointer; font: 12px/1.3 inherit;
      transition: all var(--t-fast);
    }}
    .broker button:hover {{ background: var(--accent); color: var(--accent-text); }}
    .broker button:disabled {{ opacity: 0.5; cursor: wait; }}
    .composer {{ position: relative; flex: 1 1 520px; min-width: 260px; }}
    .composer textarea {{
      width: 100%; min-height: 148px; max-height: 360px; padding-right: 40px; padding-bottom: 32px;
      background: var(--control); color: var(--text); border: 1px solid var(--border); border-radius: 10px;
      padding: 8px 10px; font: 12px/1.3 inherit; line-height: 1.45; white-space: pre-wrap; resize: vertical;
    }}
    .textarea-resize-handle {{
      position: absolute; left: 2px; bottom: 2px;
      width: 14px; height: 14px; cursor: ns-resize;
      opacity: 0.4; transition: opacity var(--t-fast);
    }}
    .textarea-resize-handle:hover {{ opacity: 0.8; }}
    .textarea-resize-handle::before {{
      content: ''; position: absolute; left: 3px; top: 0;
      width: 8px; height: 2px; background: var(--muted); border-radius: 1px;
    }}
    .textarea-resize-handle::after {{
      content: ''; position: absolute; left: 3px; bottom: 0;
      width: 8px; height: 2px; background: var(--muted); border-radius: 1px;
    }}
    .composer-toolbar {{
      position: absolute; left: 8px; bottom: 16px; right: 40px;
      display: flex; align-items: center; gap: 6px;
      font-size: 11px; color: var(--muted);
    }}
    .toolbar-select, .toolbar-btn {{
      background: rgba(255,255,255,0.03); color: var(--text); border: 1px solid var(--border);
      border-radius: var(--radius-ctrl);
      padding: 6px 12px; cursor: pointer; font: 11.5px/1.3 inherit; font-weight: 500;
      transition: color var(--t-fast), border-color var(--t-fast), box-shadow var(--t-fast), background var(--t-fast);
    }}
    .toolbar-select:hover, .toolbar-btn:hover {{ color: var(--text); border-color: rgba(113,112,255,.45); box-shadow: 0 0 0 1px var(--accent-soft); background: rgba(255,255,255,0.05); }}
    .toolbar-select {{
      appearance: none; -webkit-appearance: none;
      min-height: var(--control-h); max-width: 280px;
      padding: 6px 30px 6px 12px;
      /* Solid dark — transparent face + OS light option menu = white-on-white. */
      background-color: var(--elevated);
      background-image: var(--chevron);
      background-repeat: no-repeat;
      background-position: right 10px center;
      background-size: 12px;
      font-family: var(--mono); font-size: 11px; letter-spacing: -0.01em;
      color-scheme: dark;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
    }}
    .toolbar-select:hover {{ color: var(--text); border-color: rgba(113,112,255,.45); background-color: #1e1f21; }}
    .toolbar-select:focus, .toolbar-select:focus-visible {{
      outline: none; border-color: var(--accent) !important;
      box-shadow: 0 0 0 2px var(--accent-ring); color: var(--text);
      background-color: #1e1f21;
    }}
    .broker-model-select {{ max-width: 280px; min-width: 160px; }}
    .broker-model-status {{ color: var(--faint); font-size: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 220px; font-family: var(--mono); }}
    .picker-cluster {{
      display: inline-flex; align-items: stretch; gap: 0;
      border: 1px solid var(--border); border-radius: 8px;
      background: rgba(255,255,255,0.02); overflow: hidden;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
    }}
    .picker-cluster .toolbar-select {{
      border: 0 !important; border-radius: 0 !important; box-shadow: none !important;
      background-color: transparent !important; max-width: 200px; min-width: 110px;
    }}
    .picker-cluster .toolbar-select + .toolbar-select {{
      border-left: 1px solid var(--border) !important;
    }}
    .picker-cluster:focus-within {{
      border-color: rgba(113,112,255,.5);
      box-shadow: 0 0 0 2px var(--accent-ring);
    }}
    .picker-cluster .toolbar-select:focus, .picker-cluster .toolbar-select:focus-visible {{
      box-shadow: none !important; background: var(--accent-soft) !important;
    }}
    .toolbar-btn {{ display: inline-flex; align-items: center; gap: 4px; }}
    .toolbar-btn .icon {{ width: 14px; height: 14px; }}
    .toolbar-btn span {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100px; }}
    .toolbar-sep {{ color: var(--border); user-select: none; margin: 0 2px; }}
    .broker-folder-set {{
      background: transparent !important; border: none !important; outline: none; box-shadow: none;
      color: var(--muted); cursor: pointer; padding: 4px 6px !important; border-radius: 6px !important;
      display: inline-flex; align-items: center; justify-content: center;
      font: inherit; line-height: 1; min-width: 24px; min-height: 24px;
      transition: all var(--t-fast);
    }}
    .broker-folder-set:hover {{
      color: var(--accent) !important; border: none !important; outline: none; box-shadow: none;
      background: var(--accent-soft) !important;
      filter: drop-shadow(0 0 8px var(--accent-ring));
    }}
    .broker-folder-set:active {{
      color: var(--ok); border: none !important; outline: none;
      background: transparent !important;
      filter: drop-shadow(0 0 10px rgba(63,185,80,.6));
    }}
    .broker-folder-set.set-ok {{
      color: var(--ok); border: none !important; outline: none;
      background: transparent !important;
      filter: drop-shadow(0 0 8px rgba(63,185,80,.45));
    }}
    .broker-folder-set:focus {{ border: none !important; outline: none; box-shadow: none; background: transparent !important; }}
    #broker-folder-input:focus {{ border: none; outline: none; box-shadow: 0 0 0 1px var(--accent-ring); }}
    .composer-send {{
      display: none;
      position: absolute; right: 8px; bottom: 8px;
      width: 36px; height: 36px; min-width: 36px; padding: 0;
      background: transparent; color: var(--muted); border: none; border-radius: 7px;
      display: flex; align-items: center; justify-content: center; cursor: pointer;
      transition: all var(--t-fast);
    }}
    .composer-send .icon {{ width: 18px; height: 18px; }}
    .composer-send:hover {{ color: var(--accent); filter: drop-shadow(0 0 6px var(--accent-ring)); }}
    .composer-send.is-active {{ opacity: 1; color: var(--accent); }}
    .composer-send:disabled {{ opacity: 0.3; cursor: not-allowed; }}
    .broker-events {{
      margin-top: 10px; flex: 1 1 auto; min-height: 150px; max-height: 680px; overflow: auto;
      font: 12px/1.4 Consolas, "Cascadia Code", monospace; color: #d8d8d8;
    }}
    .broker-event {{
      border-top: 1px solid var(--border-soft); padding: 7px 0;
      display: grid; grid-template-columns: auto auto minmax(0, 1fr); gap: 7px;
      align-items: start;
      transition: background var(--t-fast);
    }}
    .broker-event:first-child {{ border-top: 0; }}
    .broker-event:hover {{ background: var(--accent-soft); }}
    .broker-event.has-error {{ background: #2a1414; }}
    .broker-event .route {{ color: var(--muted); padding-top: 2px; white-space: nowrap; }}
    .broker-event .route-ts {{ display: block; text-align: right; font-size: 9px; opacity: 0.55; margin-top: 2px; font-variant-numeric: tabular-nums; transition: color var(--t-fast), text-shadow var(--t-fast); }}
    .broker-event:hover .route-ts {{ color: #5cb85c; opacity: 1; text-shadow: 0 0 6px rgba(92,184,92,.75); }}
    .broker-event .run-counter {{ display: block; text-align: right; font-size: 9px; margin-top: 1px; color: var(--run); font-variant-numeric: tabular-nums; animation: council-run-blink 1.4s ease-in-out infinite; }}
    .broker-running {{ display: flex; align-items: center; min-height: 20px; flex-wrap: wrap; gap: 12px; padding: 4px 2px; margin-top: 4px; }}
    .broker-run-item {{ font-size: 11px; color: var(--run); text-shadow: 0 0 7px rgba(76,141,255,.65); animation: council-run-blink 1.4s ease-in-out infinite; }}
    .broker-run-item .run-counter {{ display: inline; animation: none; margin: 0; font-variant-numeric: tabular-nums; }}
    .broker-event .message {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
    .broker-badge {{
      display: inline-block; margin-right: 6px; padding: 1px 6px; border-radius: 999px;
      font-size: 9px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
      border: 1px solid currentColor; vertical-align: 1px;
    }}
    .broker-badge.timeout {{ color: var(--warn); }}
    .broker-badge.error {{ color: var(--err); }}
    .broker-error {{ margin-top: 3px; color: #ff9a9a; font-size: 10px; }}
    .broker-scroll-bottom {{
      position: absolute; left: 50%; bottom: 30px; z-index: 2;
      transform: translateX(-50%);
      width: 30px; height: 30px; min-width: 30px; padding: 0; line-height: 1;
      display: none; align-items: center; justify-content: center; font-size: 13px;
      background: transparent; color: var(--muted); border: none; border-radius: 7px;
      opacity: 0.6; cursor: pointer;
      transition: all var(--t-fast);
    }}
    .broker-scroll-bottom .icon {{ width: 24px; height: 24px; }}
    .broker-scroll-bottom:hover {{ color: var(--accent); opacity: 1; filter: drop-shadow(0 0 4px var(--accent-ring)); }}
    .broker-scroll-bottom[data-visible="1"] {{ display: inline-flex; }}
    .copy-button, .broker-event .copy-button {{
      background: transparent; color: var(--muted); border: 0 !important; outline: none; box-shadow: none;
      padding: 0; width: 14px; height: 14px; min-width: 14px;
      display: inline-flex; align-items: center; justify-content: center;
      opacity: 0.6; cursor: pointer; user-select: none; -webkit-user-select: none;
      transition: color var(--t-fast), opacity var(--t-fast), filter var(--t-fast);
    }}
    .copy-button:hover, .broker-event .copy-button:hover {{ color: var(--accent); opacity: 1; filter: drop-shadow(0 0 4px var(--accent-ring)); border: 0 !important; outline: none; box-shadow: none; background: transparent; }}
    .copy-button:focus, .copy-button:focus-visible, .broker-event .copy-button:focus, .broker-event .copy-button:focus-visible {{ outline: none; box-shadow: none; border: 0 !important; background: transparent; }}
    .copy-button.copied {{ color: var(--ok); opacity: 1; filter: drop-shadow(0 0 4px rgba(63,185,80,.4)); }}
    #agent-vis-bar {{
      display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
      padding: 10px 14px 2px; margin: 0 14px;
      border: 1px solid var(--border-soft); border-radius: 10px;
      background: rgba(255,255,255,0.02); position: sticky; top: 54px; z-index: 2;
    }}
    #agent-vis-bar .vis-label {{
      font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600;
      color: var(--faint); margin-right: 6px;
    }}
    #agent-vis-bar .vis-chip {{
      appearance: none; border: 1px solid var(--border); background: rgba(255,255,255,0.02);
      color: var(--muted); border-radius: 999px; padding: 5px 12px 5px 10px;
      font: 11px/1.2 inherit; font-weight: 500; cursor: pointer;
      display: inline-flex; align-items: center; gap: 6px;
      transition: background var(--t-fast), border-color var(--t-fast), color var(--t-fast), opacity var(--t-fast), box-shadow var(--t-fast);
    }}
    #agent-vis-bar .vis-chip::before {{
      content: ''; width: 7px; height: 7px; border-radius: 50%;
      background: var(--faint); box-shadow: inset 0 0 0 1px rgba(255,255,255,.12);
      transition: background var(--t-fast), box-shadow var(--t-fast);
    }}
    #agent-vis-bar .vis-chip:hover {{ border-color: rgba(255,255,255,0.14); color: var(--text); background: rgba(255,255,255,0.04); }}
    #agent-vis-bar .vis-chip[aria-pressed="true"] {{
      background: var(--accent-soft); border-color: rgba(113,112,255,.45); color: var(--text);
      box-shadow: 0 0 0 1px rgba(113,112,255,.12);
    }}
    #agent-vis-bar .vis-chip[aria-pressed="true"]::before {{
      background: var(--accent); box-shadow: 0 0 8px rgba(113,112,255,.55);
    }}
    #agent-vis-bar .vis-chip[aria-pressed="false"] {{ opacity: 0.5; }}
    #agent-vis-bar .vis-actions {{ margin-left: auto; display: inline-flex; gap: 6px; }}
    #agent-vis-bar .vis-all {{
      appearance: none; border: 1px solid var(--border-soft); background: rgba(255,255,255,0.02);
      color: var(--muted); border-radius: 6px; padding: 5px 10px; font: 10px/1.2 inherit; font-weight: 500; cursor: pointer;
      transition: color var(--t-fast), border-color var(--t-fast), background var(--t-fast);
    }}
    #agent-vis-bar .vis-all:hover {{ color: var(--accent-hover); border-color: rgba(113,112,255,.4); background: var(--accent-soft); }}
    main {{
      display: grid; gap: 12px; padding: 12px 14px 22px;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    }}
    .agent-card {{
      background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: var(--radius-card);
      padding: 12px 12px 10px; min-width: 0;
      animation: cardIn var(--t-slow) both;
      transition: border-color var(--t-base), box-shadow var(--t-base), opacity var(--t-base), background var(--t-base);
      display: flex; flex-direction: column; gap: 0;
    }}
    .agent-card.is-hidden {{ display: none !important; }}
    .agent-card:hover {{ border-color: rgba(113,112,255,.35); box-shadow: 0 0 0 1px rgba(113,112,255,.12); background: rgba(255,255,255,0.03); }}
    .model-field {{
      display: flex; flex-direction: column; gap: 4px; margin: 2px 0 6px;
    }}
    .model-field-label {{
      font-size: 9px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em;
      color: var(--faint); padding-left: 1px;
    }}
    @keyframes cardIn {{ from {{ opacity: 0; transform: translateY(8px); }} to {{ opacity: 1; transform: none; }} }}
    .agent-card h2 {{
      margin: 0 0 10px; font-size: 11px; font-weight: 600; color: var(--text);
      text-transform: uppercase; letter-spacing: 0.08em;
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
    }}
    .agent-title {{ display: inline-flex; align-items: center; gap: 6px; min-width: 0; }}
    .agent-name {{ display: inline-block; }}
    .agent-runtime-status {{
      display: inline-flex; align-items: center; min-height: 18px; padding: 2px 6px;
      border: 1px solid var(--border); border-radius: 3px; color: var(--muted);
      font-size: 9px; line-height: 1; text-transform: none; letter-spacing: 0;
    }}
    .agent-runtime-status.running {{ color: var(--run); border-color: rgba(76,141,255,.4); }}
    .agent-runtime-status.completed {{ color: var(--ok); border-color: rgba(63,185,80,.4); }}
    .agent-runtime-status.timeout, .agent-runtime-status.error, .agent-runtime-status.stopped {{ color: var(--err); border-color: rgba(248,81,73,.4); }}
    .agent-runtime-status.paused {{ color: var(--warn); border-color: rgba(210,153,34,.4); }}
    .agent-runtime-status.running {{ animation: council-run-blink 1s ease-in-out infinite; }}
    @keyframes council-run-blink {{ 0%, 100% {{ opacity: 1; }} 50% {{ opacity: 0.3; }} }}
    .agent-card h3 {{
      margin: 10px 0 6px; font-size: 9px; color: var(--faint);
      text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600;
      display: flex; align-items: center; justify-content: space-between; gap: 6px;
    }}
    .agent-card h3 > span:first-child {{ display: inline-block; }}
    /* Stop current worker run (icon-only) */
    .agent-pause-toggle {{
      background: transparent; border: 1px solid transparent; color: var(--muted);
      cursor: pointer; padding: 3px 7px; border-radius: 3px;
      display: inline-flex; align-items: center; justify-content: center;
      font: inherit; line-height: 1; min-width: 20px; min-height: 16px;
      opacity: 0.6; transition: all var(--t-fast);
    }}
    .agent-pause-toggle:hover {{ color: var(--accent); border-color: var(--accent); background: var(--accent-soft); opacity: 1; filter: drop-shadow(0 0 4px var(--accent-ring)); }}
    .agent-pause-toggle:disabled {{ opacity: 0.3; cursor: not-allowed; }}
    /* Collapse toggle button (icon-only) */
    .usage-collapse-toggle {{
      background: transparent; border: 1px solid transparent; color: var(--faint);
      cursor: pointer; padding: 2px 5px; border-radius: 3px;
      display: inline-flex; align-items: center; justify-content: center;
      font: inherit; line-height: 1; min-width: 18px; min-height: 14px;
      opacity: 0.6; transition: all var(--t-fast);
    }}
    .usage-collapse-toggle:hover {{ color: var(--accent); background: var(--accent-soft); opacity: 1; filter: drop-shadow(0 0 4px var(--accent-ring)); }}
    [data-field].collapsed {{ display: none !important; }}
    .reasoning-box {{ margin-top: 8px; }}
    .worker-model-dropdown {{
      margin: 0; width: 100%;
      appearance: none; -webkit-appearance: none;
      min-height: var(--control-h);
      /* Solid dark face — near-transparent rgba + light OS option menu = white-on-white. */
      background-color: var(--elevated) !important;
      background-image: var(--chevron) !important;
      background-repeat: no-repeat !important;
      background-position: right 10px center !important;
      background-size: 12px !important;
      color: var(--text) !important;
      border: 1px solid var(--border) !important;
      border-radius: var(--radius-ctrl) !important;
      padding: 7px 30px 7px 11px !important;
      font-size: 11px !important; font-family: var(--mono) !important; font-weight: 500;
      letter-spacing: -0.01em;
      outline: none !important;
      color-scheme: dark;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.03);
      transition: border-color var(--t-fast), box-shadow var(--t-fast), background var(--t-fast);
    }}
    .worker-model-dropdown:hover {{ border-color: rgba(113,112,255,.45) !important; background-color: #1e1f21 !important; }}
    .worker-model-dropdown:focus {{ border-color: var(--accent) !important; box-shadow: 0 0 0 2px var(--accent-ring); background-color: #1e1f21 !important; }}
    /* Windows native <option> popup ignores parent select bg; force dark readable text. */
    select.worker-model-dropdown option,
    select.toolbar-select option,
    .broker select option {{
      background-color: #141516;
      color: #f7f8f8;
    }}
    select.worker-model-dropdown option:checked,
    select.toolbar-select option:checked,
    .broker select option:checked {{
      background-color: #2a2b3d;
      color: #f7f8f8;
    }}
    select.worker-model-dropdown option:disabled,
    select.toolbar-select option:disabled,
    .broker select option:disabled {{
      color: #62666d;
    }}
    select.worker-model-dropdown,
    select.toolbar-select,
    .broker select {{
      color-scheme: dark;
    }}
    .worker-model-row {{
      display: grid; grid-template-columns: 78px minmax(0,1fr) auto; gap: 6px; align-items: center;
      margin: 4px 0 8px; padding: 8px; border-radius: 8px;
      background: rgba(255,255,255,0.02);
      border: 1px solid var(--border-soft);
    }}
    .worker-model-row input {{
      min-width: 0; min-height: 28px;
      background: rgba(0,0,0,0.25) !important; color: var(--text) !important;
      border: 1px solid var(--border) !important; border-radius: 6px !important;
      padding: 5px 8px !important; font-size: 11px !important; outline: none !important;
      font-family: var(--mono) !important; letter-spacing: -0.01em;
      transition: border-color var(--t-fast), box-shadow var(--t-fast);
    }}
    .worker-model-row input:focus {{ border-color: var(--accent) !important; box-shadow: 0 0 0 2px var(--accent-ring); }}
    .worker-model-row .worker-model-set {{
      background: var(--accent) !important; color: #fff !important;
      border: 1px solid transparent !important; border-radius: 6px !important;
      padding: 5px 12px !important; font-size: 11px !important; font-weight: 600;
      cursor: pointer; min-height: 28px;
      box-shadow: 0 1px 0 rgba(255,255,255,.08) inset, 0 4px 12px rgba(113,112,255,.18);
      transition: filter var(--t-fast), box-shadow var(--t-fast), background var(--t-fast);
    }}
    .worker-model-row .worker-model-set:hover {{ filter: brightness(1.08); }}
    .live-raw-pre {{ max-height: 300px; overflow: auto; overflow-x: auto; overflow-wrap: anywhere; font-family: ui-monospace, "Cascadia Code", Consolas, monospace; opacity: 0.95; }}
    .live-raw-clear {{ margin-left: 8px; padding: 0 7px; font-size: 9px; line-height: 16px; cursor: pointer;
      color: var(--muted); background: var(--control); border: 1px solid var(--border); border-radius: 4px; }}
    .live-raw-clear:hover {{ color: var(--err); border-color: var(--err); }}
    .reasoning-box summary {{ cursor: pointer; color: var(--muted); font-size: 11px; transition: color var(--t-fast); }}
    .reasoning-box summary:hover {{ color: var(--accent); }}
    .reasoning-pre {{ max-height: 220px; overflow: auto; overflow-x: auto; font-size: 10px; white-space: pre-wrap; overflow-wrap: anywhere; opacity: 0.85; }}
    .muted {{ color: var(--faint); }}
    .agent-card pre {{ overflow: auto; overflow-wrap: anywhere; max-width: 100%; }}
    .usage-card {{ background: var(--panel-soft); border: 1px solid var(--border-soft); border-radius: 8px; padding: 12px 14px; }}
    .usage-row {{
      display: flex; justify-content: space-between; align-items: baseline;
      font-size: 12px; margin: 6px 0 2px; gap: 10px;
    }}
    .usage-label {{ color: var(--muted); }}
    .usage-value {{ color: var(--text); font-variant-numeric: tabular-nums; text-align: right; }}
    .usage-section {{
      font-size: 10px; color: var(--faint); text-transform: uppercase; letter-spacing: 0.1em;
      margin: 12px 0 4px; font-weight: 600;
    }}
    .usage-bar {{
      height: 4px; background: #333; border-radius: 999px;
      overflow: hidden; margin: 4px 0 6px;
    }}
    .usage-bar.empty {{ opacity: 0.35; }}
    .usage-bar-fill {{
      height: 100%;
      background: linear-gradient(90deg, #f0f0f0 0%, #a6a6a6 100%);
      border-radius: 999px; transition: width 0.4s ease;
    }}
    .usage-footer {{
      display: flex; justify-content: flex-end; align-items: center; gap: 6px;
      margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--border-soft);
      font-size: 10px; color: var(--faint);
    }}
    .usage-model {{ color: var(--text); font-family: ui-monospace, Consolas, monospace; font-size: 11px; word-break: break-all; }}
    .usage-effort {{ color: var(--faint); }}
    pre {{
      margin: 0; padding: 8px 10px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere;
      border: 1px solid var(--border-soft); border-radius: 7px; min-height: 118px; max-height: 280px;
      font: 11px/1.4 Consolas, "Cascadia Code", monospace;
      background: #181818; color: #d8d8d8;
    }}
    .cron-panel {{ margin: 12px 14px 0; border: 1px solid var(--border); border-radius: var(--radius-card); background: var(--panel); overflow: hidden; }}
    .cron-panel summary {{ cursor: pointer; padding: 11px 14px; font-size: 12px; font-weight: 700; list-style: none; display: flex; justify-content: space-between; align-items: center; }}
    .cron-panel summary::after {{ content: '+'; margin-left: 10px; color: var(--accent-hover); font: 17px var(--mono); }}
    .cron-panel[open] summary::after {{ content: '−'; }}
    .cron-panel summary .meta {{ margin: 0 0 0 auto; font: 10px var(--mono); }}
    .cron-grid {{ display: grid; grid-template-columns: minmax(0,1fr) minmax(300px,.82fr); gap: 1px; background: var(--border); border-top: 1px solid var(--border); }}
    .cron-editor, .cron-list-wrap {{ min-width: 0; background: var(--panel); padding: 14px; }}
    .cron-form {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }}
    .cron-form .wide {{ grid-column: 1 / -1; }}
    .cron-form label {{ display: flex; flex-direction: column; gap: 5px; color: var(--muted); font-size: 10px; letter-spacing: .035em; }}
    .cron-form input, .cron-form textarea, .cron-form select {{ width: 100%; min-height: 34px; border: 1px solid var(--border); border-radius: var(--radius-ctrl); background: var(--control); color: var(--text); padding: 8px 9px; font: 11.5px var(--mono); }}
    .cron-form textarea {{ resize: vertical; line-height: 1.5; }}
    .cron-form .cron-check {{ flex-direction: row; align-items: center; align-self: end; min-height: 34px; letter-spacing: 0; }}
    .cron-check input {{ width: auto; min-height: 0; accent-color: var(--accent); }}
    .cron-actions, .cron-subhead {{ display: flex; gap: 7px; align-items: center; }}
    .cron-actions button, .cron-subhead button, .cron-job button {{ min-height: 30px; border: 1px solid var(--border); border-radius: var(--radius-ctrl); background: var(--elevated); color: var(--text); padding: 6px 9px; cursor: pointer; font: 11px var(--font); }}
    .cron-actions button:hover, .cron-subhead button:hover, .cron-job button:hover {{ border-color: var(--accent); }}
    .cron-actions .primary {{ background: var(--accent); border-color: var(--accent); }}
    .cron-status {{ min-height: 18px; color: var(--muted); font: 10.5px var(--mono); }}
    .cron-status.is-error {{ color: var(--err); }}
    .cron-subhead {{ justify-content: space-between; margin-bottom: 8px; color: var(--muted); font: 10px var(--mono); text-transform: uppercase; letter-spacing: .08em; }}
    .cron-list, .cron-runs {{ display: flex; flex-direction: column; gap: 7px; max-height: 255px; overflow: auto; }}
    .cron-runs {{ max-height: 155px; }}
    .cron-history-head {{ margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border); }}
    .cron-job, .cron-run {{ border: 1px solid var(--border); border-radius: var(--radius-ctrl); padding: 9px; background: #0a0c0f; }}
    .cron-job-head {{ display: flex; justify-content: space-between; gap: 8px; align-items: center; }}
    .cron-job-name {{ font-size: 12px; font-weight: 700; }}
    .cron-badge {{ color: var(--muted); font: 9.5px var(--mono); }}
    .cron-job-meta, .cron-run {{ color: var(--muted); font: 10.5px/1.45 var(--mono); overflow-wrap: anywhere; }}
    .cron-job-meta {{ margin: 5px 0 8px; }}
    .cron-empty {{ color: var(--muted); padding: 22px 10px; text-align: center; border: 1px dashed var(--border); border-radius: var(--radius-ctrl); font: 11px var(--mono); }}
    @media (max-width: 900px) {{ .cron-grid {{ grid-template-columns: 1fr; }} .cron-form {{ grid-template-columns: 1fr; }} .cron-form .wide {{ grid-column: auto; }} }}
    .narrator-panel {{
      position: fixed; top: 0; right: 0; bottom: 0; width: var(--narr-w); max-width: 70vw; min-width: 260px; z-index: 60;
      display: flex; flex-direction: column;
      background: var(--panel); border-left: 1px solid var(--border);
      box-shadow: -12px 0 32px rgba(0,0,0,.45);
      font: 12px/1.45 var(--font);
      transition: transform var(--t-base), visibility var(--t-base);
    }}
    body.narrator-hidden .narrator-panel {{ transform: translateX(100%); visibility: hidden; pointer-events: none; }}
    .narrator-resize {{ position: absolute; left: -5px; top: 0; bottom: 0; width: 10px; cursor: ew-resize; z-index: 2; touch-action: none; }}
    .narrator-resize:hover, .narrator-resize.is-dragging {{ background: var(--accent-soft); }}
    .narrator-toggle {{
      position: fixed; right: calc(var(--narr-w) + 12px); bottom: 12px; z-index: 70; width: 36px; height: 36px;
      padding: 0; border: 1px solid var(--border); border-radius: 50%;
      background: var(--elevated); color: var(--text); cursor: pointer; font: 700 20px/1 var(--mono);
      box-shadow: 0 8px 24px rgba(0,0,0,.35); transition: right var(--t-base), background var(--t-fast), border-color var(--t-fast);
    }}
    body.narrator-hidden .narrator-toggle {{ right: 12px; }}
    .narrator-toggle:hover {{ border-color: var(--accent); background: var(--accent-soft); }}
    .narrator-settings {{ display: flex; flex-direction: column; gap: 9px; flex: 0 0 auto; }}
    .narrator-settings[hidden] {{ display: none; }}
    .narrator-settings-toggle {{
      margin-left: auto; width: 28px; height: 28px; padding: 0; border: 1px solid var(--border); border-radius: var(--radius-ctrl);
      background: var(--control); color: var(--muted); cursor: pointer; font-size: 13px; line-height: 1;
    }}
    .narrator-settings-toggle:hover {{ color: var(--text); border-color: var(--accent); }}
    .narrator-panel::before {{
      content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
      background: linear-gradient(180deg, var(--accent) 0%, rgba(113,112,255,.25) 55%, transparent 100%);
      pointer-events: none;
    }}
    .narrator-head {{
      display: flex; align-items: center; justify-content: space-between; gap: 8px;
      padding: 14px 16px 12px 18px; font-weight: 600;
      background: linear-gradient(180deg, rgba(113,112,255,.16), rgba(113,112,255,.03));
      border-bottom: 1px solid var(--border-soft); flex: 0 0 auto;
      letter-spacing: 0.01em; font-size: 13px;
    }}
    .narrator-head .narr-title {{ display: inline-flex; align-items: center; gap: 8px; }}
    .narrator-head .narr-badge {{
      font-size: 9px; font-weight: 600; color: var(--accent-hover);
      text-transform: uppercase; letter-spacing: 0.1em;
      padding: 3px 8px; border-radius: 999px;
      border: 1px solid rgba(113,112,255,.35); background: var(--accent-soft);
    }}
    .narrator-body {{ padding: 14px 16px 16px 18px; display: flex; flex-direction: column; gap: 8px; flex: 1 1 auto; min-height: 0; }}
    .narrator-label {{ font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: .08em; color: var(--faint); }}
    #narrator-panel .toolbar-select {{
      width: 100%; max-width: none; min-height: var(--control-h);
      background-color: rgba(255,255,255,0.03);
      border: 1px solid var(--border); border-radius: var(--radius-ctrl);
      color: var(--text); padding: 7px 30px 7px 11px;
      font-family: var(--mono); font-size: 11px;
    }}
    .narrator-btn {{
      padding: 10px 12px; border-radius: 8px; border: 1px solid transparent;
      background: var(--accent);
      color: #fff; font-weight: 600; cursor: pointer; letter-spacing: 0.01em;
      box-shadow: 0 1px 0 rgba(255,255,255,.08) inset, 0 8px 20px rgba(113,112,255,.22);
      transition: filter var(--t-fast), box-shadow var(--t-fast);
    }}
    .narrator-btn:hover {{ filter: brightness(1.08); }}
    .narrator-btn:disabled {{ opacity: .55; cursor: progress; filter: none; box-shadow: none; }}
    .narrator-prompt-row {{ display: flex; align-items: center; justify-content: space-between; }}
    .narrator-reset {{ background: none; border: none; color: var(--accent-hover); cursor: pointer; font-size: 10.5px; text-decoration: none; padding: 2px 4px; border-radius: 4px; }}
    .narrator-reset:hover {{ background: var(--accent-soft); }}
    .narrator-prompt {{
      width: 100%; box-sizing: border-box; resize: vertical; min-height: 72px; max-height: 40vh;
      padding: 9px 11px; border-radius: 8px; border: 1px solid var(--border);
      background: rgba(0,0,0,.28); color: inherit; font: 11px/1.45 var(--mono);
    }}
    .narrator-prompt:focus {{ outline: none; border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-ring); }}
    .narrator-meta {{ font-size: 10.5px; min-height: 12px; color: var(--muted); font-family: var(--mono); }}
    .narrator-out {{
      white-space: pre-wrap; overflow-wrap: anywhere; flex: 1 1 auto; min-height: 140px; overflow: auto;
      padding: 12px 13px; border-radius: 8px; border: 1px solid var(--border);
      background: rgba(0,0,0,.32); font-size: 12px; line-height: 1.55;
    }}
    .narrator-out .nsec {{ font-weight: 700; color: var(--accent-hover); display: block; margin-top: 6px; }}
    .narrator-out .nsec:first-child {{ margin-top: 0; }}
    /* Council Precision v2 · Monitor / Operate surface · visual layer only */
    :root {{
      --bg: #08090b; --panel: #101216; --panel-soft: #13161b;
      --control: #181b20; --elevated: #20242b;
      --border: rgba(255,255,255,.08); --border-soft: rgba(255,255,255,.05);
      --text: #f3f4f6; --muted: #a4aab4; --faint: #707783;
      --accent: #7c6ff2; --accent-hover: #958bf7;
      --accent-soft: rgba(124,111,242,.12); --accent-ring: rgba(124,111,242,.30);
      --ok: #42c98a; --run: #8b80f6; --warn: #dfa851; --err: #eb6971;
      --t-fast: 120ms cubic-bezier(.2,.8,.2,1);
      --t-base: 170ms cubic-bezier(.2,.8,.2,1);
      --t-slow: 200ms cubic-bezier(.2,.8,.2,1);
      --radius-ctrl: 7px; --radius-card: 10px; --control-h: 36px;
      --narr-w: 320px;
      --font: "Segoe UI Variable", "Inter Variable", Inter, "Segoe UI", system-ui, sans-serif;
    }}
    body {{
      font-size: 14px;
      line-height: 1.5;
      background-color: var(--bg);
      background-image:
        radial-gradient(circle at 12% 18%, rgba(124,92,192,.18) 0, rgba(124,92,192,0) 38%),
        radial-gradient(circle at 82% 30%, rgba(45,148,255,.12) 0, rgba(45,148,255,0) 36%),
        radial-gradient(circle at 50% 100%, rgba(14,165,233,.08) 0, rgba(14,165,233,0) 40%);
      background-attachment: fixed;
    }}
    header {{ padding: 14px 18px; background: rgba(8,9,11,.9); border-color: var(--border); backdrop-filter: blur(14px) saturate(1.15); }}
    h1 {{ font-size: 18px; font-weight: 660; letter-spacing: -.025em; }}
    .meta {{ margin-top: 2px; font-size: 12px; }}
    .clear-session-button {{ min-height: 32px; padding: 6px 10px; border-radius: var(--radius-ctrl); font-size: 12px; transition: background var(--t-fast), color var(--t-fast), border-color var(--t-fast); }}
    :where(button, select, textarea, input):focus-visible {{ outline: none; border-color: var(--accent) !important; box-shadow: 0 0 0 3px var(--accent-ring) !important; }}
    .broker {{ top: 68px; margin: 12px 14px 0; padding: 14px; border-radius: var(--radius-card); background: var(--panel); box-shadow: 0 10px 32px rgba(0,0,0,.22); }}
    .broker:focus-within {{ box-shadow: 0 0 0 1px var(--accent-ring), 0 12px 34px rgba(0,0,0,.26); }}
    .broker-row {{ gap: 9px; }}
    .broker select, .broker textarea, .composer textarea {{ border-radius: var(--radius-ctrl); background: var(--control); font-size: 13px; }}
    .composer textarea {{ min-height: 120px; padding: 12px 44px 38px 12px; line-height: 1.55; }}
    .composer-toolbar {{ left: 9px; bottom: 10px; gap: 6px; font-size: 12px; }}
    .toolbar-select, .toolbar-btn {{ min-height: 32px; padding: 7px 10px; font-size: 12px; }}
    .toolbar-select {{ font-family: var(--font); }}
    .broker-model-status {{ font-size: 11px; }}
    .picker-cluster {{ border-radius: var(--radius-ctrl); background: var(--control); }}
    .composer-send {{ right: 8px; bottom: 8px; border-radius: var(--radius-ctrl); background: var(--accent); color: #fff; }}
    .composer-send:hover {{ background: var(--accent-hover); color: #fff; filter: none; transform: translateY(-1px); }}
    .composer-send:disabled {{ background: var(--elevated); color: var(--faint); transform: none; }}
    .broker-events {{ margin-top: 10px; font: 11.5px/1.5 var(--mono); }}
    .broker-event {{ padding: 8px 3px; border-color: var(--border-soft); border-radius: 3px; }}
    .broker-event:hover {{ background: rgba(255,255,255,.022); }}
    .broker-event.has-error {{ background: rgba(235,105,113,.07); }}
    .broker-event .route-ts, .broker-event .run-counter {{ font-size: 10px; }}
    .broker-run-item {{ text-shadow: none; }}
    #agent-vis-bar {{ top: 56px; margin: 0 14px; padding: 8px 10px; border-radius: var(--radius-card); background: var(--panel); }}
    #agent-vis-bar .vis-label {{ font-size: 10px; letter-spacing: .08em; }}
    #agent-vis-bar .vis-chip {{ min-height: 29px; padding: 5px 10px; font-size: 12px; }}
    #agent-vis-bar .vis-chip[aria-pressed="true"] {{ box-shadow: none; }}
    #agent-vis-bar .vis-chip[aria-pressed="true"]::before {{ box-shadow: none; }}
    #agent-vis-bar .vis-all {{ min-height: 29px; padding: 5px 9px; border-radius: var(--radius-ctrl); font-size: 11px; }}
    main {{
      display: flex; align-items: flex-start; gap: 12px; padding: 12px 14px 24px;
      overflow-x: auto; overflow-y: visible;
    }}
    .agent-card {{
      flex: 1 1 0; width: 0; min-width: 154px;
      align-self: start; padding: 14px; border-radius: var(--radius-card); background: var(--panel); box-shadow: 0 1px 0 rgba(255,255,255,.018);
    }}
    .agent-card.is-selected {{ flex-grow: 2.25; }}
    .agent-card:hover {{ border-color: rgba(255,255,255,.12); background: #12151a; box-shadow: 0 9px 26px rgba(0,0,0,.18); transform: translateY(-1px); }}
    .agent-card h2 {{ margin-bottom: 10px; font-size: 14px; text-transform: none; letter-spacing: -.015em; white-space: nowrap; }}
    .agent-card h3 {{ margin: 14px -14px 8px; padding: 11px 14px 0; border-top: 1px solid var(--border); color: var(--text); font-size: 12px; font-weight: 700; letter-spacing: .08em; }}
    .model-field-label {{ font-size: 10px; letter-spacing: .08em; }}
    .agent-runtime-status {{ gap: 6px; min-height: 24px; padding: 4px 8px; border: 0; border-radius: 999px; background: var(--elevated); font-size: 11px; }}
    .agent-runtime-status::before {{ content: ''; width: 6px; height: 6px; flex: 0 0 6px; border-radius: 50%; background: currentColor; }}
    .agent-runtime-status.running {{ animation: none; }}
    .agent-runtime-status.running::before {{ animation: council-status-pulse 1.6s ease-in-out infinite; }}
    @keyframes council-status-pulse {{ 50% {{ opacity: .35; transform: scale(.72); }} }}
    .worker-model-dropdown {{ min-height: 36px; border-radius: var(--radius-ctrl) !important; padding: 8px 28px 8px 10px !important; font-size: 12px !important; }}
    .worker-model-row {{ flex-wrap: wrap; padding: 8px 0; margin: 4px 0; border: 0; border-top: 1px solid var(--border-soft); border-radius: 0; background: transparent; }}
    .worker-model-row input {{ min-width: 0; min-height: 34px; background: var(--control) !important; font-size: 11.5px !important; }}
    .worker-model-row .worker-model-set {{ min-height: 34px; border-radius: var(--radius-ctrl) !important; box-shadow: none; }}
    pre {{ min-height: 72px; max-height: 320px; padding: 12px; border: 1px solid var(--border-soft); border-radius: var(--radius-ctrl); background: #0a0c0f; color: #d8dce3; font: 12.5px/1.62 var(--mono); }}
    .reasoning-box {{ margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border-soft); color: var(--muted); }}
    .reasoning-box summary {{ font-size: 11px; }}
    .reasoning-pre {{ max-height: 240px; font-size: 11px; opacity: .72; }}
    .usage-card {{ padding: 8px 0 0; border: 0; border-top: 1px solid var(--border-soft); border-radius: 0; background: transparent; }}
    .usage-row {{ color: var(--muted); font-size: 11px; }}
    .usage-bar {{ background: var(--elevated); }}
    .usage-bar-fill {{ background: var(--accent); transition: width var(--t-slow); }}
    .narrator-panel {{ background: #0d0f13; box-shadow: -14px 0 36px rgba(0,0,0,.3); font-size: 13px; }}
    .narrator-panel::before {{ width: 1px; background: var(--border); }}
    .narrator-head {{ padding: 15px 16px; background: transparent; font-size: 15px; letter-spacing: -.015em; }}
    .narrator-head .narr-badge {{ padding: 2px 6px; border-color: var(--border); background: var(--elevated); color: var(--muted); font-size: 9px; }}
    .narrator-body {{ padding: 14px 16px 16px; gap: 9px; }}
    .narrator-label {{ font-size: 10px; letter-spacing: .08em; }}
    #narrator-panel .toolbar-select {{ min-height: 36px; background-color: var(--control); font-size: 12px; }}
    .narrator-btn {{ min-height: 38px; border-radius: var(--radius-ctrl); box-shadow: none; transition: transform var(--t-fast), background var(--t-fast), opacity var(--t-fast); }}
    .narrator-btn:hover {{ background: var(--accent-hover); filter: none; transform: translateY(-1px); }}
    .narrator-btn:disabled {{ cursor: not-allowed; background: var(--elevated); color: var(--faint); opacity: .72; transform: none; }}
    .narrator-prompt {{ min-height: 82px; padding: 10px; border-radius: var(--radius-ctrl); background: var(--control); font: 12px/1.5 var(--font); }}
    .narrator-meta {{ font-size: 10.5px; }}
    .narrator-out {{ padding: 12px 13px; border-radius: var(--radius-ctrl); background: #090b0e; font-size: 13px; line-height: 1.62; }}
    @media (max-width: 1100px) {{ :root {{ --narr-w: 288px; }} }}
    @media (max-width: 720px) {{
      :root {{ --narr-w: min(92vw, 420px); }} body {{ padding-right: 0; }}
      .narrator-panel {{ position: fixed; width: var(--narr-w); max-width: 92vw; min-width: 260px; min-height: 0; border-left: 1px solid var(--border); border-top: 0; }}
      .narrator-resize {{ display: none; }}
      .broker, #agent-vis-bar {{ position: relative; top: auto; margin: 9px; }}
      main {{ display: grid; padding: 9px; overflow: visible; grid-template-columns: 1fr; }}
      .agent-card, .agent-card.is-selected {{ width: auto; min-width: 0; }}
    }}
    @media (min-width: 721px) {{
      main {{ display: flex; }}
    }}
    @media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ scroll-behavior: auto !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; }} }}
  </style>
  <script>
    const COLLAPSE_STORAGE_KEY = 'council_collapse_v1';
    let brokerForceScrollBottom = false;
    let brokerEventsSignature = '';
    let brokerModelWritePending = false;
    const brokerModelOptionsByAgent = {{}};
    const BROKER_NEAR_BOTTOM_PX = 48;
    const councilRuntimeByAgent = {{}};
    const councilRunningSlots = {{}};
    const councilChannels = {{
      activity: {{
        endpoint: '/api/council/activity?fields=' + encodeURIComponent([
          'status','runtime','paused','paused_at','paused_reason','direct_log',
          'direct_log_path','direct_log_mtime','latest_res','model'
        ].join(',')),
        interval: 2000,
        version: 0,
        generation: '',
        items: new Map(),
        inFlight: false,
        nextAt: 0,
        collection: 'agents',
      }},
      state: {{
        endpoint: '/api/council/state',
        interval: 30000,
        version: 0,
        generation: '',
        items: new Map(),
        inFlight: false,
        nextAt: 0,
        collection: 'agents',
      }},
      events: {{
        endpoint: '/api/council/events?limit=40',
        interval: 5000,
        version: 0,
        generation: '',
        items: new Map(),
        inFlight: false,
        nextAt: 0,
        collection: 'events',
      }},
    }};
    const councilScheduledTasks = new Map();
    let councilSchedulerTimer = null;
    let councilSchedulerStarted = false;
    function councilRequestUrl(channel) {{
      const url = new URL(channel.endpoint, window.location.origin);
      url.searchParams.set('since_version', String(channel.version || 0));
      url.searchParams.set('generation', channel.generation || '');
      return url.pathname + url.search;
    }}
    function stableCouncilEventSort(events) {{
      const eventTime = event => {{
        const parsed = event && event.ts ? Date.parse(event.ts) : NaN;
        if (Number.isFinite(parsed)) return parsed;
        const mtime = Number(event && event.mtime);
        return Number.isFinite(mtime) ? mtime * 1000 : 0;
      }};
      return events.map((event, index) => ({{ event, index }}))
        .sort((left, right) => eventTime(left.event) - eventTime(right.event) || left.index - right.index)
        .map(row => row.event);
    }}
    function materializeCouncilPayload(channel, payload) {{
      const collection = channel.collection;
      const incoming = Array.isArray(payload[collection]) ? payload[collection] : [];
      const isFull = payload.full === true || payload.delta !== true || channel.version <= 0;
      if (isFull) channel.items.clear();
      if (!isFull) {{
        for (const id of payload.removed_ids || []) channel.items.delete(String(id));
      }}
      for (const item of incoming) {{
        const id = String((item && item.id) || '');
        if (id) channel.items.set(id, item);
      }}
      const nextVersion = Number(payload.version);
      if (Number.isFinite(nextVersion) && nextVersion >= 0) channel.version = nextVersion;
      if (typeof payload.generation === 'string') channel.generation = payload.generation;
      let items = Array.from(channel.items.values());
      if (collection === 'events') items = stableCouncilEventSort(items);
      const materialized = Object.assign({{}}, payload);
      materialized[collection] = items;
      materialized.full = true;
      materialized.delta = false;
      materialized.materialized = true;
      return materialized;
    }}
    async function refreshCouncilChannel(name) {{
      const channel = councilChannels[name];
      if (!channel || channel.inFlight) return null;
      channel.inFlight = true;
      try {{
        const response = await fetch(councilRequestUrl(channel), {{ cache: 'no-store' }});
        if (response.status === 304) return null;
        if (!response.ok) throw new Error(`${{name}} fetch failed: ${{response.status}}`);
        const payload = await response.json();
        return materializeCouncilPayload(channel, payload);
      }} finally {{
        channel.inFlight = false;
      }}
    }}
    function queueCouncilScheduler(delay) {{
      if (!councilSchedulerStarted || document.hidden) return;
      if (councilSchedulerTimer !== null) clearTimeout(councilSchedulerTimer);
      councilSchedulerTimer = setTimeout(runCouncilScheduler, Math.max(0, delay || 0));
    }}
    function runCouncilScheduler() {{
      councilSchedulerTimer = null;
      if (document.hidden) return;
      const now = Date.now();
      let soonest = now + 60000;
      for (const [name, task] of councilScheduledTasks) {{
        if (task.nextAt <= now) {{
          task.nextAt = now + task.interval;
          if (!task.schedulerInFlight) {{
            task.schedulerInFlight = true;
            Promise.resolve().then(task.run).catch(err => {{
              console.error(`scheduled council task failed: ${{name}}`, err);
            }}).finally(() => {{
              task.schedulerInFlight = false;
              queueCouncilScheduler(0);
            }});
          }}
        }}
        soonest = Math.min(soonest, task.nextAt);
      }}
      queueCouncilScheduler(Math.max(25, soonest - Date.now()));
    }}
    function scheduleCouncilTask(name, interval, run, state) {{
      const task = state || {{}};
      task.interval = Math.max(25, Number(interval) || 1000);
      task.run = run;
      task.nextAt = 0;
      task.schedulerInFlight = false;
      councilScheduledTasks.set(name, task);
      if (councilSchedulerStarted) queueCouncilScheduler(0);
    }}
    function startCouncilScheduler() {{
      if (councilSchedulerStarted) return;
      councilSchedulerStarted = true;
      document.addEventListener('visibilitychange', () => {{
        if (document.hidden) {{
          if (councilSchedulerTimer !== null) clearTimeout(councilSchedulerTimer);
          councilSchedulerTimer = null;
          return;
        }}
        // Force an immediate refresh without resetting channel versions/generations.
        for (const task of councilScheduledTasks.values()) task.nextAt = 0;
        queueCouncilScheduler(0);
      }});
      for (const task of councilScheduledTasks.values()) task.nextAt = 0;
      queueCouncilScheduler(0);
    }}
    function isBrokerNearBottom(box) {{
      return (box.scrollHeight - box.scrollTop - box.clientHeight) <= BROKER_NEAR_BOTTOM_PX;
    }}
    function updateBrokerScrollButton(box) {{
      box = box || document.getElementById('broker-events');
      const btn = document.getElementById('broker-scroll-bottom');
      if (!box || !btn) return;
      const visible = box.scrollHeight > box.clientHeight && !isBrokerNearBottom(box);
      btn.dataset.visible = visible ? '1' : '0';
      btn.setAttribute('aria-hidden', visible ? 'false' : 'true');
    }}
    function scrollBrokerToBottom(box) {{
      box = box || document.getElementById('broker-events');
      if (box) {{ box.scrollTop = box.scrollHeight; updateBrokerScrollButton(box); }}
    }}
    /* Keep wheel input inside text/log areas until they reach a boundary,
       then forward the wheel delta to the Council page. */
    function setupScrollChaining() {{
      document.querySelectorAll(
        'textarea, pre, [contenteditable="true"], [data-scroll-chain]'
      ).forEach(el => {{
        if (el.dataset.scrollBound === '1') return;
        el.dataset.scrollBound = '1';
        el.addEventListener('wheel', ev => {{
          const atTop    = el.scrollTop <= 0;
          const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 1;
          const goingUp   = ev.deltaY < 0;
          const goingDown = ev.deltaY > 0;
          if ((goingUp && atTop) || (goingDown && atBottom)) {{
            // Element is at boundary — let the next wheel event chain to
            // the page (document.body).  We do NOT call preventDefault()
            // so the native scroll on <body> / <main> takes over.
            ev.preventDefault();
            ev.stopPropagation();
            window.scrollBy({{ left: ev.deltaX, top: ev.deltaY, behavior: 'auto' }});
            return;
          }}
        }}, {{ passive: false }});
      }});
    }}

    function updateBrokerSendState() {{
      const targetEl = document.getElementById('broker-to');
      const button = document.getElementById('broker-send');
      const input = document.getElementById('broker-text');
      const modelSelect = document.getElementById('broker-model');
      if (!targetEl || !button) return;
      const hasText = input && input.value.trim().length > 0;
      const selectedModel = modelSelect && !modelSelect.hidden ? (modelSelect.value || '').trim() : '';
      const selectedSlot = brokerRuntimeKey(targetEl.value, selectedModel);
      const slotRunning = !!councilRunningSlots[selectedSlot];
      const blocked = brokerModelWritePending || slotRunning;
      button.disabled = blocked;
      if (modelSelect) modelSelect.disabled = brokerModelWritePending;
      button.classList.toggle('is-active', hasText && !blocked);
    }}
    function brokerRuntimeKey(agent, model) {{
      const base = String(agent || '').trim();
      const selectedModel = String(model || '').trim();
      if (!selectedModel) return base;
      const safeModel = selectedModel.replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 96);
      return base + '::' + (safeModel || 'model');
    }}
    function brokerModelLabel(model) {{
      const text = String(model || '');
      return text.length > 36 ? text.slice(0, 16) + '...' + text.slice(-17) : text;
    }}
    function runtimeRunningItems(agentId, runtime) {{
      const rows = [];
      const seen = {{}};
      const add = (slot) => {{
        if (!slot) return;
        const key = String(slot.runtime_key || brokerRuntimeKey(agentId, slot.model || '') || agentId);
        if (!key || seen[key]) return;
        seen[key] = true;
        rows.push({{
          agent: agentId,
          runtime_key: key,
          model: slot.model || '',
          provider: slot.provider || '',
          started_at: slot.started_at || runtime.started_at || null,
        }});
      }};
      if (runtime && runtime.state === 'running') add(runtime);
      for (const slot of (runtime && runtime.active_slots) || []) add(slot);
      return rows.filter(row => row.started_at);
    }}
    async function loadBrokerModelOptions(agent) {{
      if (!agent) return null;
      if (brokerModelOptionsByAgent[agent]) return brokerModelOptionsByAgent[agent];
      const response = await fetch('/api/council/valid-models?agent=' + encodeURIComponent(agent), {{ cache: 'no-store' }});
      const data = await response.json().catch(() => ({{ ok: false }}));
      if (!response.ok || !data.ok) throw new Error(data.error || `valid-models failed: ${{response.status}}`);
      brokerModelOptionsByAgent[agent] = data;
      return data;
    }}
    function setBrokerModelStatus(text) {{
      const status = document.getElementById('broker-model-status');
      if (status) status.textContent = text || '';
    }}
    async function updateBrokerModelDropdown() {{
      const targetEl = document.getElementById('broker-to');
      const select = document.getElementById('broker-model');
      if (!targetEl || !select) return;
      const agent = targetEl.value;
      select.replaceChildren();
      select.hidden = true;
      setBrokerModelStatus('');
      try {{
        const data = await loadBrokerModelOptions(agent);
        const options = (data && data.models) || [];
        if (options.length <= 1) {{
          const current = (data && data.current && data.current.model) || (options[0] && options[0].model) || '';
          if (current) setBrokerModelStatus('model: ' + brokerModelLabel(current));
          return;
        }}
        options.forEach(item => {{
          const opt = document.createElement('option');
          opt.value = item.model || '';
          opt.textContent = brokerModelLabel(item.model || '');
          opt.dataset.provider = item.provider || '';
          opt.title = `${{item.provider || ''}} / ${{item.model || ''}}`;
          if (data.current && data.current.model === item.model) opt.selected = true;
          select.appendChild(opt);
        }});
        select.hidden = false;
        setBrokerModelStatus('applies to next dispatch');
      }} catch (err) {{
        setBrokerModelStatus('model list unavailable');
      }} finally {{
        updateBrokerSendState();
      }}
    }}
    async function setBrokerModelOverride() {{
      const targetEl = document.getElementById('broker-to');
      const select = document.getElementById('broker-model');
      if (!targetEl || !select || select.hidden) return;
      const agent = targetEl.value;
      const selected = select.selectedOptions && select.selectedOptions[0];
      const model = (select.value || '').trim();
      const provider = ((selected && selected.dataset.provider) || '').trim();
      if (!agent || !model) return;
      brokerModelWritePending = true;
      setBrokerModelStatus('setting model...');
      updateBrokerSendState();
      try {{
        const response = await fetch('/api/council/worker-config', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ agent, provider, model }}),
        }});
        const data = await response.json().catch(() => ({{ ok: false, error: 'bad json' }}));
        if (!response.ok || !data.ok) throw new Error(data.error || response.status);
        brokerModelOptionsByAgent[agent] = Object.assign({{}}, brokerModelOptionsByAgent[agent] || {{}}, {{
          current: {{ provider: data.provider || provider, model }},
        }});
        setBrokerModelStatus('next: ' + brokerModelLabel(model));
      }} catch (err) {{
        setBrokerModelStatus('model set failed');
        window.alert(`Model set failed: ${{err.message}}`);
      }} finally {{
        brokerModelWritePending = false;
        updateBrokerSendState();
      }}
    }}
    function loadCollapseMap() {{
      try {{ return JSON.parse(localStorage.getItem(COLLAPSE_STORAGE_KEY) || '{{}}'); }}
      catch (e) {{ return {{}}; }}
    }}
    function saveCollapseMap(map) {{
      try {{ localStorage.setItem(COLLAPSE_STORAGE_KEY, JSON.stringify(map)); }}
      catch (e) {{ /* ignore quota errors */ }}
    }}
    function setToggleVisual(btn, isCollapsed) {{
      const target = btn.getAttribute('data-target');
      const fieldLabel = (target || '').replace('_', ' ');
      btn.setAttribute('aria-expanded', String(!isCollapsed));
      btn.setAttribute('aria-label', isCollapsed ? `Expand ${{fieldLabel}}` : `Collapse ${{fieldLabel}}`);
      btn.setAttribute('title', isCollapsed ? 'Expand' : 'Collapse');
    }}
    function setupCollapseToggles() {{
      document.querySelectorAll('.usage-collapse-toggle').forEach(btn => {{
        if (btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', () => {{
          const target = btn.getAttribute('data-target');
          const card = btn.closest('.agent-card');
          if (!card) return;
          const el = card.querySelector(`[data-field="${{target}}"]`);
          if (!el) return;
          const isCollapsed = el.classList.toggle('collapsed');
          setToggleVisual(btn, isCollapsed);
          const agent = card.getAttribute('data-agent');
          if (agent) {{
            const map = loadCollapseMap();
            map[`${{agent}}:${{target}}`] = isCollapsed;
            saveCollapseMap(map);
          }}
        }});
      }});
    }}
    function applyStoredCollapse() {{
      const map = loadCollapseMap();
      Object.entries(map).forEach(([key, isCollapsed]) => {{
        const idx = key.indexOf(':');
        if (idx < 0) return;
        const agent = key.substring(0, idx);
        const target = key.substring(idx + 1);
        const card = findAgentCard(agent);
        if (!card) return;
        const btn = card.querySelector(`.usage-collapse-toggle[data-target="${{target}}"]`);
        const el = card.querySelector(`[data-field="${{target}}"]`);
        if (!btn || !el) return;
        if (isCollapsed) {{
          el.classList.add('collapsed');
          setToggleVisual(btn, true);
        }}
      }});
    }}
    function setStopButtonVisual(btn, stateName) {{
      const agent = btn.getAttribute('data-agent');
      const isRunning = stateName === 'running';
      btn.setAttribute('data-action', 'stop');
      btn.setAttribute('aria-label', `Stop ${{agent}}`);
      btn.setAttribute('title', isRunning ? `Stop ${{agent}}` : `No active run for ${{agent}}`);
      btn.disabled = !isRunning;
    }}
    function setupPauseButtons() {{
      document.querySelectorAll('.agent-pause-toggle').forEach(btn => {{
        if (btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', async (ev) => {{
          ev.preventDefault();
          const agent = btn.getAttribute('data-agent');
          const action = 'stop';
          const endpoint = '/api/council/stop';
          if (!agent) return;
          const confirmMsg = `Stop ${{agent}}'s current run? Send another prompt to start a fresh run.`;
          if (!window.confirm(confirmMsg)) return;
          btn.disabled = true;
          try {{
            const resp = await fetch(endpoint, {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ agent }}),
            }});
            const data = await resp.json().catch(() => ({{ ok: false, error: 'bad json' }}));
            if (!resp.ok || !data.ok) {{
              window.alert(`${{action}} failed: ${{data.error || resp.status}}`);
              return;
            }}
            // Optimistic UI update; full state reconciles on next 1s refresh.
            councilRuntimeByAgent[agent] = {{ state: 'stopped' }};
            setStopButtonVisual(btn, 'stopped');
            updateBrokerSendState();
            refreshCouncilActivity();
            refreshCouncilUsage();
          }} catch (err) {{
            window.alert(`${{action}} error: ${{err.message}}`);
          }} finally {{
            setStopButtonVisual(btn, (councilRuntimeByAgent[agent] || {{}}).state || 'idle');
          }}
        }});
      }});
    }}
    async function copyText(text, button) {{
      try {{
        await navigator.clipboard.writeText(text || '');
        if (button) {{
          button.classList.add('copied');
          setTimeout(() => {{ button.classList.remove('copied'); }}, 900);
        }}
      }} catch (err) {{
        window.alert(`Copy failed: ${{err.message}}`);
      }}
    }}
    function setupCopyButtons() {{
      document.querySelectorAll('.copy-button[data-copy-field]').forEach(btn => {{
        if (btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', () => {{
          const card = btn.closest('.agent-card');
          const field = btn.getAttribute('data-copy-field');
          const el = card && field ? card.querySelector(`[data-field="${{field}}"]`) : null;
          if (el) copyText(el.textContent || '', btn);
        }});
      }});
    }}
    function setupClearSessionButton() {{
      const btn = document.getElementById('clear-council-session');
      if (!btn || btn.dataset.bound === '1') return;
      btn.dataset.bound = '1';
      btn.addEventListener('click', async () => {{
        if (!window.confirm('Clear the current Council session from the UI? Provider-global histories are preserved.')) return;
        btn.disabled = true;
        try {{
          const response = await fetch('/api/council/clear-session', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ confirm: true }}),
          }});
          const data = await response.json().catch(() => ({{ ok: false, error: 'bad json' }}));
          if (!response.ok || !data.ok) {{
            window.alert(`Clear session failed: ${{data.error || response.status}}`);
            return;
          }}
          const broker = document.getElementById('broker-events');
          if (broker) broker.innerHTML = '';
          refreshCouncilEvents();
          refreshCouncilActivity();
          refreshCouncilUsage();
        }} catch (err) {{
          window.alert(`Clear session error: ${{err.message}}`);
        }} finally {{
          btn.disabled = false;
        }}
      }});
    }}
    function findAgentCard(agentId) {{
      return Array.from(document.querySelectorAll('.agent-card')).find(
        card => card.getAttribute('data-agent') === String(agentId || '')
      ) || null;
    }}
    function rebuildCouncilRunningSlots() {{
      for (const key of Object.keys(councilRunningSlots)) delete councilRunningSlots[key];
      for (const runtime of Object.values(councilRuntimeByAgent)) {{
        if (!runtime) continue;
        if (runtime.state === 'running' && runtime.runtime_key) {{
          councilRunningSlots[runtime.runtime_key] = true;
        }}
        for (const slot of runtime.active_slots || []) {{
          if (slot && slot.runtime_key) councilRunningSlots[slot.runtime_key] = true;
        }}
      }}
    }}
    function applyCouncilPayload(state, includeMetrics) {{
      const generated = document.getElementById('generated-at');
      if (generated) generated.textContent = state.generated_at || 'unknown';
      const agents = Array.isArray(state.agents) ? state.agents : [];
      const nextRuntimeByAgent = {{}};
      for (const agent of agents) {{
        if (!agent || !agent.id) continue;
        nextRuntimeByAgent[agent.id] = agent.runtime || {{ state: 'idle' }};
      }}
      // state.agents is the channel's full materialized Map, even when the wire
      // response was a delta. Rebuild slots only from that complete runtime view.
      for (const key of Object.keys(councilRuntimeByAgent)) delete councilRuntimeByAgent[key];
      Object.assign(councilRuntimeByAgent, nextRuntimeByAgent);
      rebuildCouncilRunningSlots();
      for (const agent of agents) {{
        const card = findAgentCard(agent.id);
        if (!card) continue;
        const name = card.querySelector('.agent-name');
        if (name) name.textContent = agent.label || agent.id;
        const stateName = agent.paused ? 'paused' : ((agent.runtime || {{}}).state || 'idle');
        const status = card.querySelector('[data-field="runtime_status"]');
        if (status) {{
          status.textContent = stateName;
          status.className = `agent-runtime-status ${{stateName}}`;
        }}
        if (includeMetrics) {{
          const metrics = card.querySelector('[data-field="metrics"]');
          if (metrics) metrics.innerHTML = agent.metrics_html || '';
        }}
        for (const field of ['latest_res', 'reasoning', 'live_raw', 'model']) {{
          if (!(field in agent)) continue;
          const el = card.querySelector(`[data-field="${{field}}"]`);
          if (!el) continue;
          const text = agent[field] || '';
          // live_raw is a tail that grows during a run: follow the newest output
          // only when the user is already pinned near the bottom (so manual
          // scroll-up to read earlier output is preserved).
          const follow = field === 'live_raw' &&
            (el.scrollHeight - el.scrollTop - el.clientHeight) < 24;
          if (el.textContent !== text) el.textContent = text;
          el.classList.toggle('muted', !agent[field]);
          if (follow) el.scrollTop = el.scrollHeight;
        }}
        const pauseBtn = card.querySelector('.agent-pause-toggle');
        if (pauseBtn) setStopButtonVisual(pauseBtn, stateName);
      }}
      updateBrokerSendState();
    }}
    async function refreshCouncilActivity() {{
      try {{
        const state = await refreshCouncilChannel('activity');
        if (state) applyCouncilPayload(state, false);
      }} catch (err) {{
        const generated = document.getElementById('generated-at');
        if (generated) generated.textContent = `error: ${{err.message}}`;
      }}
    }}
    async function refreshCouncilUsage() {{
      try {{
        const state = await refreshCouncilChannel('state');
        if (state) applyCouncilPayload(state, true);
      }} catch (err) {{
        const generated = document.getElementById('generated-at');
        if (generated) generated.textContent = `error: ${{err.message}}`;
      }}
    }}
    function escapeHtml(text) {{
      return String(text || '').replace(/[&<>"']/g, ch => ({{
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }}[ch]));
    }}
    async function refreshCouncilEvents() {{
      const box = document.getElementById('broker-events');
      if (!box) return;
      try {{
        const data = await refreshCouncilChannel('events');
        if (!data) {{
          if (brokerForceScrollBottom) {{ scrollBrokerToBottom(box); brokerForceScrollBottom = false; }}
          updateBrokerScrollButton(box);
          return;
        }}
        const events = stableCouncilEventSort(data.events || []).filter(ev => {{
          const from = String(ev.from || '').toLowerCase();
          const to = String(ev.to || '').toLowerCase();
          if (ev.type === 'system' || ev.type === 'notice') return false;
          if ((from === 'a' || from === '[a]' || from === 'orchestrator') && !to) return false;
          return true;
        }}).slice(-40);
        // Task B: a broker timeout/error must surface its error even when ev.text exists.
        const errOf = ev => String(ev.error || '').trim();
        const signature = events.map(ev =>
          `${{ev.id || ''}}|${{(ev.text || '').length}}|${{errOf(ev)}}`
        ).join('~');
        // Task C: only rebuild the transcript when content actually changed. A no-op
        // refresh must not clobber the user's manual scroll position or text selection.
        if (signature === brokerEventsSignature) {{
          if (brokerForceScrollBottom) {{ scrollBrokerToBottom(box); brokerForceScrollBottom = false; }}
          updateBrokerScrollButton(box);
          return;
        }}
        // Task C: follow new content only when the user is already near the bottom
        // (or just sent a message); otherwise preserve their reading position above.
        const wasNearBottom = isBrokerNearBottom(box);
        const previousScrollTop = box.scrollTop;
        const previousScrollHeight = box.scrollHeight;
        brokerEventsSignature = signature;
        box.innerHTML = events.map((ev, idx) => {{
          const _lbl = (v, fallback) => fallback || (v ? (() => {{ const n = document.querySelector(`[data-agent="${{v}}"] .agent-name`); return (n && n.textContent) || v; }})() : '?');
          const route = ev.type === 'direct_log'
            ? `[log] ${{_lbl(ev.from, ev.from_label)}} -> ${{ev.path || 'direct-log'}}`
            : `${{_lbl(ev.from, ev.from_label)}} -> ${{_lbl(ev.to, ev.to_label)}}`;
          const err = errOf(ev);
          const isTimeout = /timeout/i.test(err);
          const badge = err
            ? `<span class="broker-badge ${{isTimeout ? 'timeout' : 'error'}}">${{escapeHtml(isTimeout ? 'timeout' : 'error')}}</span>`
            : '';
          const text = ev.text || '';
          const _d = ev.ts ? new Date(ev.ts) : (ev.mtime ? new Date(ev.mtime * 1000) : null);
          const _p2 = n => String(n).padStart(2, '0');
          const tsStr = (_d && !isNaN(_d.getTime())) ? `${{_p2(_d.getHours())}}:${{_p2(_d.getMinutes())}}-${{_p2(_d.getDate())}}${{_p2(_d.getMonth()+1)}}${{String(_d.getFullYear()).slice(-2)}}` : '';
          const errLine = err ? `<div class="broker-error">error: ${{escapeHtml(err)}}</div>` : '';
          return `<div class="broker-event${{err ? ' has-error' : ''}}"><div class="route">${{escapeHtml(route)}}${{tsStr ? `<span class="route-ts">${{tsStr}}</span>` : ''}}</div><button class="copy-button" type="button" data-copy-event="${{idx}}" aria-label="Copy message" title="Copy message"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg></button><div class="message" id="broker-msg-${{idx}}">${{badge}}${{escapeHtml(text)}}${{errLine}}</div></div>`;
        }}).join('');
        box.querySelectorAll('[data-copy-event]').forEach(btn => {{
          btn.addEventListener('click', () => {{
            const msg = box.querySelector(`#broker-msg-${{btn.getAttribute('data-copy-event')}}`);
            if (msg) copyText(msg.textContent || '', btn);
          }});
        }});
        if (brokerForceScrollBottom || wasNearBottom) {{
          scrollBrokerToBottom(box);
          brokerForceScrollBottom = false;
        }} else {{
          // User is reading older messages above: keep their position stable.
          const heightDelta = box.scrollHeight - previousScrollHeight;
          box.scrollTop = Math.max(0, previousScrollTop + heightDelta);
        }}
        updateBrokerScrollButton(box);
      }} catch (err) {{
        // keep the previous transcript
      }}
    }}
    function setupBroker() {{
      const form = document.getElementById('broker-form');
      if (!form || form.dataset.bound === '1') return;
      form.dataset.bound = '1';
      form.addEventListener('submit', async (ev) => {{
        ev.preventDefault();
        const target = document.getElementById('broker-to').value;
        const modeEl = document.getElementById('broker-mode');
        const mode = modeEl ? modeEl.value : 'broker';
        const projectPath = '';
        const input = document.getElementById('broker-text');
        const button = document.getElementById('broker-send');
        const text = (input.value || '').trim();
        if (!target || !text) return;
        if (brokerModelWritePending) return;
        const modelSelect = document.getElementById('broker-model');
        const selectedModel = modelSelect && !modelSelect.hidden ? (modelSelect.value || '').trim() : '';
        const selectedOption = modelSelect && modelSelect.selectedOptions ? modelSelect.selectedOptions[0] : null;
        const selectedProvider = selectedOption ? ((selectedOption.dataset.provider || '').trim()) : '';
        const selectedSlot = brokerRuntimeKey(target, selectedModel);
        if (councilRunningSlots[selectedSlot]) return;
        const startedAt = Date.now() / 1000;
        const previousRuntime = councilRuntimeByAgent[target] || {{}};
        const optimisticSlots = ((previousRuntime.active_slots || []).filter(
          slot => slot && slot.runtime_key !== selectedSlot
        ));
        optimisticSlots.push({{
          runtime_key: selectedSlot,
          model: selectedModel,
          provider: selectedProvider,
          started_at: startedAt,
        }});
        councilRuntimeByAgent[target] = Object.assign({{}}, previousRuntime, {{
          state: 'running',
          runtime_key: selectedSlot,
          model: selectedModel,
          provider: selectedProvider,
          started_at: startedAt,
          active_slots: optimisticSlots,
        }});
        councilRunningSlots[selectedSlot] = true;
        input.value = '';
        updateBrokerSendState();
        try {{
          const response = await fetch('/api/council/message', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ from: 'Anh', to: target, text, mode, project_path: projectPath, timeout_sec: 6000, provider: selectedProvider, model: selectedModel }}),
          }});
          const data = await response.json().catch(() => ({{ ok: false, error: 'bad json' }}));
          if (!response.ok || !data.reply) {{
            window.alert(`Broker failed: ${{data.error || response.status}}`);
          }}
          brokerForceScrollBottom = true;
          refreshCouncilEvents();
          refreshCouncilActivity();
          refreshCouncilUsage();
        }} catch (err) {{
          window.alert(`Broker error: ${{err.message}}`);
        }} finally {{
          delete councilRunningSlots[selectedSlot];
          if ((councilRuntimeByAgent[target] || {{}}).state === 'running' && (councilRuntimeByAgent[target] || {{}}).runtime_key === selectedSlot) {{
            const currentRuntime = councilRuntimeByAgent[target] || {{}};
            const remainingSlots = (currentRuntime.active_slots || []).filter(
              slot => slot && slot.runtime_key !== selectedSlot
            );
            if (remainingSlots.length) {{
              const nextSlot = remainingSlots[remainingSlots.length - 1] || {{}};
              councilRuntimeByAgent[target] = Object.assign({{}}, currentRuntime, {{
                state: 'running',
                runtime_key: nextSlot.runtime_key || target,
                model: nextSlot.model || '',
                provider: nextSlot.provider || '',
                started_at: nextSlot.started_at || currentRuntime.started_at,
                active_slots: remainingSlots,
              }});
            }} else {{
              councilRuntimeByAgent[target] = {{ state: 'idle' }};
            }}
          }}
          updateBrokerSendState();
        }}
      }});
      const targetEl = document.getElementById('broker-to');
      if (targetEl && targetEl.dataset.busybound !== '1') {{
        targetEl.dataset.busybound = '1';
        targetEl.addEventListener('change', () => {{
          updateBrokerModelDropdown();
          updateBrokerSendState();
        }});
      }}
      const modelSelect = document.getElementById('broker-model');
      if (modelSelect && modelSelect.dataset.modelbound !== '1') {{
        modelSelect.dataset.modelbound = '1';
        modelSelect.addEventListener('change', setBrokerModelOverride);
      }}
      const input = document.getElementById('broker-text');
      if (input && input.dataset.keybound !== '1') {{
        input.dataset.keybound = '1';
        input.addEventListener('input', updateBrokerSendState);
        input.addEventListener('keydown', ev => {{
          if (ev.key === 'Enter' && !ev.shiftKey) {{
            ev.preventDefault();
            form.requestSubmit();
          }}
        }});
      }}
      // Jump-to-latest control stays centered at the broker bottom and only
      // appears while the user is reading above the latest event.
      const scrollBtn = document.getElementById('broker-scroll-bottom');
      const eventsBox = document.getElementById('broker-events');
      if (eventsBox && eventsBox.dataset.buttonBound !== '1') {{
        eventsBox.dataset.buttonBound = '1';
        eventsBox.addEventListener('scroll', () => updateBrokerScrollButton(eventsBox));
      }}
      if (scrollBtn && scrollBtn.dataset.bound !== '1') {{
        scrollBtn.dataset.bound = '1';
        scrollBtn.addEventListener('click', () => {{
          brokerForceScrollBottom = true;
          scrollBrokerToBottom(eventsBox);
        }});
      }}
      updateBrokerScrollButton(eventsBox);
      // Broker resize handle
      const resizeHandle = document.getElementById('broker-resize');
      const brokerSection = document.querySelector('.broker');
      if (resizeHandle && brokerSection && resizeHandle.dataset.bound !== '1') {{
        resizeHandle.dataset.bound = '1';
        let startY, startHeight;
        resizeHandle.addEventListener('mousedown', (e) => {{
          e.preventDefault();
          startY = e.clientY;
          startHeight = brokerSection.offsetHeight;
          const onMouseMove = (e) => {{
            const delta = e.clientY - startY;
            const newHeight = Math.max(120, Math.min(Math.floor(window.innerHeight * 0.92), startHeight + delta));
            brokerSection.style.maxHeight = newHeight + 'px';
            brokerSection.style.overflow = 'auto';
          }};
          const onMouseUp = () => {{
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
          }};
          document.addEventListener('mousemove', onMouseMove);
          document.addEventListener('mouseup', onMouseUp);
        }});
      }}
    }}
    function setupWorkerModels() {{
      fetch('/api/council/worker-config').then(r=>r.json()).then(d=>{{
        const cfg=(d&&d.config)||{{}};
        document.querySelectorAll('.worker-model-set').forEach(btn=>{{
          const agent=btn.getAttribute('data-agent');
          const card=btn.closest('.agent-card');
          const pv=document.querySelector('.worker-provider[data-agent="'+agent+'"]');
          const mv=document.querySelector('.worker-model[data-agent="'+agent+'"]');
          if(cfg[agent]){{ if(pv)pv.value=cfg[agent].provider||''; if(mv)mv.value=cfg[agent].model||''; }}
          if(btn.dataset.bound==='1')return;
          btn.dataset.bound='1';
          if(card) card.addEventListener('click', ()=>{{
            document.querySelectorAll('.agent-card.is-selected').forEach(el=>{{ if(el!==card) el.classList.remove('is-selected'); }});
            card.classList.add('is-selected');
          }});
          btn.addEventListener('click', async ()=>{{
            const provider=((pv&&pv.value)||'').trim();
            const model=((mv&&mv.value)||'').trim();
            const original=btn.textContent;
            btn.disabled=true;
            btn.textContent='...';
            btn.title='';
            try{{
              const res=await fetch('/api/council/worker-config',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{agent,provider,model}})}});
              const j=await res.json().catch(()=>({{ok:false,error:'Invalid server response'}}));
              if(!res.ok || !j.ok) throw new Error(j.error||j.error_name||('HTTP '+res.status));
              if(pv) pv.value=j.provider||provider;
              if(mv) mv.value=j.model||model;
              btn.textContent='✓';
              btn.title='Saved: '+(j.model||model);
            }}catch(e){{
              btn.textContent='Error';
              btn.title=(e&&e.message)||'Unable to save model';
              console.error('worker model set failed', agent, e);
            }} finally {{
              setTimeout(()=>{{ btn.textContent=original||'Set'; btn.disabled=false; }},1800);
            }}
          }});
        }});
      }}).catch(e=>console.error('worker model config load failed',e));
    }}
    function updateBrokerRunning() {{
      const box = document.getElementById('broker-running');
      if (!box) return;
      const now = Date.now() / 1000;
      const items = [];
      for (const a in councilRuntimeByAgent) {{
        const r = councilRuntimeByAgent[a] || {{}};
        const nameEl = document.querySelector(`[data-agent="${{a}}"] .agent-name`);
        const label = (nameEl && nameEl.textContent) || a;
        for (const row of runtimeRunningItems(a, r)) {{
          const el = Math.max(0, Math.floor(now - row.started_at));
          const mm = String(Math.floor(el / 60)).padStart(2, '0');
          const ss = String(el % 60).padStart(2, '0');
          const model = row.model ? ` · ${{brokerModelLabel(row.model)}}` : '';
          items.push(`<span class="broker-run-item">${{escapeHtml(label)}}${{escapeHtml(model)}} running <span class="run-counter">${{mm}}:${{ss}}</span></span>`);
        }}
      }}
      box.innerHTML = items.join('');
    }}
    function setupLiveRawClear() {{
      document.querySelectorAll('.live-raw-clear').forEach(btn => {{
        if (btn.dataset.bound === '1') return;
        btn.dataset.bound = '1';
        btn.addEventListener('click', async (ev) => {{
          ev.preventDefault();
          ev.stopPropagation();  // don't toggle the <details>
          const agent = btn.getAttribute('data-agent');
          if (!agent) return;
          try {{
            await fetch('/api/council/live-raw-clear', {{
              method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ agent }}),
            }});
          }} catch (e) {{}}
          const card = findAgentCard(agent);
          const pre = card && card.querySelector('[data-field="live_raw"]');
          if (pre) {{ pre.textContent = ''; pre.classList.add('muted'); }}
        }});
      }});
    }}
    function setupWorkerModelDropdown() {{
      document.querySelectorAll('.worker-model-dropdown').forEach(sel => {{
        if (sel.dataset.bound === '1') return;
        sel.dataset.bound = '1';
        sel.addEventListener('change', async () => {{
          const agent = sel.getAttribute('data-agent');
          const model = sel.value;
          if (!agent || !model) return;
          const pv = document.querySelector('.worker-provider[data-agent="' + agent + '"]');
          const provider = ((pv && pv.value) || '').trim();
       try {{
       const res = await fetch('/api/council/worker-config', {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
         body: JSON.stringify({{ agent, provider, model }}) }});
       const data = await res.json().catch(() => ({{ ok: false }}));
       if (!res.ok || !data.ok) throw new Error(data.error || res.status);
       brokerModelOptionsByAgent[agent] = Object.assign({{}}, brokerModelOptionsByAgent[agent] || {{}}, {{
         current: {{ provider: data.provider || provider, model }},
       }});
       const mv = document.querySelector('.worker-model[data-agent="' + agent + '"]');
       if (mv) mv.value = model;
       if ((document.getElementById('broker-to') || {{}}).value === agent) updateBrokerModelDropdown();
     }} catch (e) {{}}
        }});
      }});
    }}
    document.addEventListener('DOMContentLoaded', () => {{
      applyStoredCollapse();
      setupCollapseToggles();
      setupPauseButtons();
      setupClearSessionButton();
      setupCopyButtons();
      setupBroker();
      setupWorkerModels();
      setupWorkerModelDropdown();
      updateBrokerModelDropdown();
      setupLiveRawClear();
      setupScrollChaining();
      setupAgentVisibility();
      setupNarrator();
      setupCronJobs();
      scheduleCouncilTask('activity', councilChannels.activity.interval, refreshCouncilActivity, councilChannels.activity);
      scheduleCouncilTask('state', councilChannels.state.interval, refreshCouncilUsage, councilChannels.state);
      scheduleCouncilTask('events', councilChannels.events.interval, refreshCouncilEvents, councilChannels.events);
      scheduleCouncilTask('broker-running', 1000, updateBrokerRunning);
      startCouncilScheduler();
    }});



    function setupAgentVisibility() {{
      const bar = document.getElementById('agent-vis-bar');
      if (!bar) return;
      const KEY = 'council_agent_vis_v1';
      function loadMap() {{
        try {{
          const raw = localStorage.getItem(KEY);
          if (!raw) return {{}};
          const j = JSON.parse(raw);
          return (j && typeof j === 'object') ? j : {{}};
        }} catch (e) {{ return {{}}; }}
      }}
      function saveMap(map) {{
        try {{ localStorage.setItem(KEY, JSON.stringify(map)); }} catch (e) {{}}
      }}
      const cards = Array.from(document.querySelectorAll('.agent-card[data-agent]'));
      if (!cards.length) return;
      bar.querySelectorAll('.vis-chip,.vis-actions').forEach(n => n.remove());
      const map = loadMap();
      cards.forEach(card => {{
        const agent = card.getAttribute('data-agent');
        if (!agent) return;
        const labelEl = card.querySelector('.agent-name');
        const label = (labelEl && labelEl.textContent.trim()) || agent;
        const visible = map[agent] !== false;
        card.classList.toggle('is-hidden', !visible);
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'vis-chip';
        chip.dataset.agent = agent;
        chip.textContent = label;
        chip.setAttribute('aria-pressed', visible ? 'true' : 'false');
        chip.title = (visible ? 'Hide ' : 'Show ') + label;
        chip.addEventListener('click', () => {{
          const now = chip.getAttribute('aria-pressed') !== 'true';
          chip.setAttribute('aria-pressed', now ? 'true' : 'false');
          chip.title = (now ? 'Hide ' : 'Show ') + label;
          card.classList.toggle('is-hidden', !now);
          const m = loadMap();
          m[agent] = now;
          saveMap(m);
        }});
        bar.appendChild(chip);
      }});
      const actions = document.createElement('div');
      actions.className = 'vis-actions';
      function setAll(show) {{
        const m = loadMap();
        bar.querySelectorAll('.vis-chip').forEach(chip => {{
          const agent = chip.dataset.agent;
          chip.setAttribute('aria-pressed', show ? 'true' : 'false');
          const label = chip.textContent;
          chip.title = (show ? 'Hide ' : 'Show ') + label;
          const card = document.querySelector('.agent-card[data-agent="' + agent + '"]');
          if (card) card.classList.toggle('is-hidden', !show);
          m[agent] = show;
        }});
        saveMap(m);
      }}
      const showAll = document.createElement('button');
      showAll.type = 'button'; showAll.className = 'vis-all'; showAll.textContent = 'Show all';
      showAll.addEventListener('click', () => setAll(true));
      const hideAll = document.createElement('button');
      hideAll.type = 'button'; hideAll.className = 'vis-all'; hideAll.textContent = 'Hide all';
      hideAll.addEventListener('click', () => setAll(false));
      actions.appendChild(showAll);
      actions.appendChild(hideAll);
      bar.appendChild(actions);
    }}

    function renderNarration(text) {{
      // Structured display: bold the 3 section headers, keep the rest as-is.
      const secs = ['SUMMARY', 'FLOW', 'HIGHLIGHTS', 'TÓM TẮT', 'LUỒNG', 'ĐIỂM TƯỜNG THUẬT'];
      const NL = String.fromCharCode(10);
      const lines = String(text || '').split(NL);
      const frag = document.createDocumentFragment();
      lines.forEach((ln, i) => {{
        const trimmed = ln.trim();
        const hit = secs.find(s => trimmed.toUpperCase().startsWith(s));
        if (hit) {{
          const b = document.createElement('span');
          b.className = 'nsec';
          b.textContent = ln;
          frag.appendChild(b);
        }} else {{
          frag.appendChild(document.createTextNode(ln));
        }}
        if (i < lines.length - 1) frag.appendChild(document.createTextNode(NL));
      }});
      const outEl = document.getElementById('narrator-out');
      outEl.textContent = '';
      outEl.appendChild(frag);
    }}

    function setupCronJobs() {{
      const form = document.getElementById('cron-form');
      const list = document.getElementById('cron-list');
      const runs = document.getElementById('cron-runs');
      const status = document.getElementById('cron-status');
      if (!form || !list || !runs || !status) return;
      let jobs = [];
      let loading = false;
      const byId = id => document.getElementById(id);
      const clean = value => escapeHtml(String(value == null ? '' : value));
      async function loadCronModels(agentId, selectId, preferred) {{
        const select = byId(selectId); select.innerHTML = '<option value="">— loading —</option>';
        if (!agentId) {{ select.innerHTML = '<option value="">— select worker first —</option>'; return; }}
        try {{
          const data = await api('/api/council/valid-models?agent=' + encodeURIComponent(agentId));
          select.innerHTML = (data.models || []).map(item => '<option value="' + clean(item.model) + '" data-provider="' + clean(item.provider || '') + '">' + clean(item.model) + '</option>').join('');
          if (preferred) select.value = preferred;
        }} catch (error) {{ select.innerHTML = '<option value="">model list unavailable</option>'; }}
      }}
      const setStatus = (message, error) => {{
        status.textContent = message || '';
        status.classList.toggle('is-error', !!error);
      }};
      async function api(url, options) {{
        const response = await fetch(url, Object.assign({{cache: 'no-store'}}, options || {{}}));
        const data = await response.json().catch(() => ({{ok: false, error: 'Invalid server response'}}));
        if (!response.ok || !data.ok) throw new Error(data.error || ('HTTP ' + response.status));
        return data;
      }}
      function reset() {{
        form.reset();
        byId('cron-id').value = '';
        byId('cron-schedule-type').value = 'interval';
        byId('cron-schedule-value').value = '1h';
        byId('cron-timeout').value = '3600';
        byId('cron-enabled').checked = true;
        byId('cron-narrator-enabled').checked = false;
        loadCronModels(byId('cron-worker').value, 'cron-worker-model', '');
        loadCronModels(byId('cron-narrator-worker').value, 'cron-narrator-model', '');
        setStatus('');
      }}
      function fill(job) {{
        byId('cron-id').value = job.id;
        byId('cron-name').value = job.name;
        byId('cron-worker').value = job.worker;
        loadCronModels(job.worker, 'cron-worker-model', job.worker_model || '');
        byId('cron-schedule-type').value = job.schedule_type;
        byId('cron-schedule-value').value = job.schedule_value;
        byId('cron-prompt').value = job.prompt;
        byId('cron-timeout').value = job.timeout_sec || 3600;
        byId('cron-enabled').checked = !!job.enabled;
        byId('cron-narrator-enabled').checked = !!job.narrator_enabled;
        byId('cron-narrator-worker').value = job.narrator_worker || job.worker;
        loadCronModels(job.narrator_worker || job.worker, 'cron-narrator-model', job.narrator_model || '');
        byId('cron-narrator-prompt').value = job.narrator_prompt || '';
        setStatus('Editing ' + job.name);
        form.scrollIntoView({{behavior: 'smooth', block: 'nearest'}});
      }}
      function renderJobs() {{
        if (!jobs.length) {{
          list.innerHTML = '<div class="cron-empty">No schedules yet. Create one on the left.</div>';
          return;
        }}
        list.innerHTML = jobs.map(job =>
          '<article class="cron-job">' +
          '<div class="cron-job-head"><span class="cron-job-name">' + clean(job.name) + '</span>' +
          '<span class="cron-badge">' + (job.enabled ? 'ACTIVE' : 'PAUSED') + '</span></div>' +
          '<div class="cron-job-meta">' + clean((job.workers || [job.worker]).join(', ')) + ' · ' + clean(job.schedule_type) + ':' +
          clean(job.schedule_value) + ' · next ' + clean(job.next_run_at ? new Date(job.next_run_at).toLocaleString() : 'disabled') + '</div>' +
          '<div class="cron-actions"><button type="button" data-cron-run="' + clean(job.id) + '">Run now</button>' +
          '<button type="button" data-cron-edit="' + clean(job.id) + '">Edit</button>' +
          '<button type="button" data-cron-delete="' + clean(job.id) + '">Delete</button></div></article>'
        ).join('');
      }}
      function renderRuns(items) {{
        if (!items.length) {{
          runs.innerHTML = '<div class="cron-empty">No run history.</div>';
          return;
        }}
        runs.innerHTML = items.map(run =>
          '<div class="cron-run"><b>' + clean(run.status.toUpperCase()) + '</b> · ' + clean(run.trigger_kind) +
          ' · ' + clean(run.started_at ? new Date(run.started_at).toLocaleString() : new Date(run.created_at).toLocaleString()) +
          (run.error ? '<br><span class="cron-status is-error">' + clean(run.error) + '</span>' : '') + '</div>'
        ).join('');
      }}
      async function load() {{
        if (loading) return;
        loading = true;
        list.innerHTML = '<div class="cron-empty">Loading jobs…</div>';
        runs.innerHTML = '<div class="cron-empty">Loading history…</div>';
        try {{
          const values = await Promise.all([
            api('/api/council/cron/jobs'),
            api('/api/council/cron/runs?limit=20')
          ]);
          jobs = values[0].jobs || [];
          renderJobs();
          renderRuns(values[1].runs || []);
        }} catch (error) {{
          list.innerHTML = '<div class="cron-empty">Could not load jobs: ' + clean(error.message || error) + '</div>';
          runs.innerHTML = '<div class="cron-empty">History unavailable.</div>';
          setStatus('Load error: ' + (error.message || error), true);
        }} finally {{
          loading = false;
        }}
      }}
      form.addEventListener('submit', async event => {{
        event.preventDefault();
        const jobId = byId('cron-id').value;
        const payload = {{
          name: byId('cron-name').value,
          worker: byId('cron-worker').value,
          workers: [byId('cron-worker').value],
          worker_model: byId('cron-worker-model').value,
          worker_provider: (byId('cron-worker-model').selectedOptions[0] || {{}}).dataset ? ((byId('cron-worker-model').selectedOptions[0] || {{}}).dataset.provider || '') : '',
          schedule_type: byId('cron-schedule-type').value,
          schedule_value: byId('cron-schedule-value').value,
          prompt: byId('cron-prompt').value,
          timeout_sec: Number(byId('cron-timeout').value),
          enabled: byId('cron-enabled').checked,
          narrator_enabled: byId('cron-narrator-enabled').checked,
          narrator_worker: byId('cron-narrator-worker').value,
          narrator_model: byId('cron-narrator-model').value,
          narrator_provider: (byId('cron-narrator-model').selectedOptions[0] || {{}}).dataset ? ((byId('cron-narrator-model').selectedOptions[0] || {{}}).dataset.provider || '') : '',
          narrator_prompt: byId('cron-narrator-prompt').value
        }};
        setStatus('Saving…');
        try {{
          await api('/api/council/cron/jobs' + (jobId ? '/' + encodeURIComponent(jobId) : ''), {{
            method: jobId ? 'PUT' : 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(payload)
          }});
          reset();
          setStatus('Saved');
          await load();
        }} catch (error) {{
          setStatus('Save error: ' + (error.message || error), true);
        }}
      }});
      list.addEventListener('click', async event => {{
        const button = event.target.closest('button');
        if (!button) return;
        const editId = button.getAttribute('data-cron-edit');
        if (editId) {{ const job = jobs.find(item => item.id === editId); if (job) fill(job); return; }}
        const runId = button.getAttribute('data-cron-run');
        const deleteId = button.getAttribute('data-cron-delete');
        if (deleteId && !confirm('Delete this schedule? Run history remains in SQLite.')) return;
        const id = runId || deleteId;
        if (!id) return;
        button.disabled = true;
        setStatus(runId ? 'Triggering…' : 'Deleting…');
        try {{
          await api('/api/council/cron/jobs/' + encodeURIComponent(id) + (runId ? '/trigger' : ''), {{
            method: runId ? 'POST' : 'DELETE'
          }});
          setStatus(runId ? 'Run queued' : 'Deleted');
          await load();
        }} catch (error) {{
          setStatus('Action error: ' + (error.message || error), true);
        }} finally {{
          button.disabled = false;
        }}
      }});
      byId('cron-reset').addEventListener('click', reset);
      byId('cron-refresh').addEventListener('click', load);
      byId('cron-worker').addEventListener('change', event => loadCronModels(event.target.value, 'cron-worker-model', ''));
      byId('cron-narrator-worker').addEventListener('change', event => loadCronModels(event.target.value, 'cron-narrator-model', ''));
      byId('cron-schedule-type').addEventListener('change', event => {{
        const input = byId('cron-schedule-value');
        input.placeholder = event.target.value === 'cron' ? '0 9 * * 1-5' : '15m';
      }});
      reset();
      scheduleCouncilTask('cron-jobs', 15000, load);
    }}

    function setupNarrator() {{
      const panel = document.getElementById('narrator-panel');
      const sel = document.getElementById('narrator-agent');
      const modelSel = document.getElementById('narrator-model');
      const btn = document.getElementById('narrator-btn');
      const out = document.getElementById('narrator-out');
      const meta = document.getElementById('narrator-meta');
      const promptEl = document.getElementById('narrator-prompt');
      const resetBtn = document.getElementById('narrator-prompt-reset');
      const toggle = document.getElementById('narrator-toggle');
      const resize = document.getElementById('narrator-resize');
      const settings = document.getElementById('narrator-settings');
      const settingsToggle = document.getElementById('narrator-settings-toggle');
      if (!panel || !toggle || !sel || !btn) return;
      const AGENT_KEY = 'council_narrator_agent_v1';
      const MODEL_KEY = 'council_narrator_model_v1';
      const PROMPT_KEY = 'council_narrator_prompt_v1';
      const VISIBLE_KEY = 'council_narrator_visible_v1';
      const WIDTH_KEY = 'council_narrator_width_v1';
      const SETTINGS_KEY = 'council_narrator_settings_v1';
      const setSettingsVisible = (visible) => {{
        if (!settings || !settingsToggle) return;
        settings.hidden = !visible;
        settingsToggle.textContent = visible ? '▲' : '▼';
        settingsToggle.title = visible ? 'Hide narrator settings' : 'Show narrator settings';
        settingsToggle.setAttribute('aria-label', visible ? 'Hide narrator settings' : 'Show narrator settings');
        settingsToggle.setAttribute('aria-expanded', visible ? 'true' : 'false');
        try {{ localStorage.setItem(SETTINGS_KEY, visible ? '1' : '0'); }} catch (e) {{}}
      }};
      const setVisible = (visible) => {{
        document.body.classList.toggle('narrator-hidden', !visible);
        toggle.textContent = visible ? '−' : '+';
        toggle.title = visible ? 'Hide Narrator' : 'Show Narrator';
        toggle.setAttribute('aria-label', visible ? 'Hide Narrator' : 'Show Narrator');
        toggle.setAttribute('aria-expanded', visible ? 'true' : 'false');
        panel.setAttribute('aria-hidden', visible ? 'false' : 'true');
        try {{ localStorage.setItem(VISIBLE_KEY, visible ? '1' : '0'); }} catch (e) {{}}
      }};
      let visible = true;
      try {{ visible = localStorage.getItem(VISIBLE_KEY) !== '0'; }} catch (e) {{}}
      try {{
        const savedWidth = parseInt(localStorage.getItem(WIDTH_KEY) || '', 10);
        if (Number.isFinite(savedWidth)) document.documentElement.style.setProperty('--narr-w', Math.max(260, Math.min(savedWidth, window.innerWidth * .7)) + 'px');
      }} catch (e) {{}}
      setVisible(visible);
      let settingsVisible = true;
      try {{ settingsVisible = localStorage.getItem(SETTINGS_KEY) !== '0'; }} catch (e) {{}}
      setSettingsVisible(settingsVisible);
      toggle.addEventListener('click', () => setVisible(document.body.classList.contains('narrator-hidden')));
      if (settingsToggle) settingsToggle.addEventListener('click', () => setSettingsVisible(settings ? settings.hidden : true));
      if (resize) {{
        resize.addEventListener('pointerdown', ev => {{
          if (window.innerWidth <= 720) return;
          ev.preventDefault(); resize.setPointerCapture(ev.pointerId); resize.classList.add('is-dragging');
          const move = e => {{ const width = Math.max(260, Math.min(window.innerWidth - e.clientX, window.innerWidth * .7)); document.documentElement.style.setProperty('--narr-w', width + 'px'); }};
          const stop = e => {{
            resize.classList.remove('is-dragging'); resize.releasePointerCapture(e.pointerId);
            resize.removeEventListener('pointermove', move); resize.removeEventListener('pointerup', stop); resize.removeEventListener('pointercancel', stop);
            try {{ localStorage.setItem(WIDTH_KEY, Math.round(panel.getBoundingClientRect().width)); }} catch (err) {{}}
          }};
          resize.addEventListener('pointermove', move); resize.addEventListener('pointerup', stop); resize.addEventListener('pointercancel', stop);
        }});
      }}
      const defaultPrompt = promptEl ? promptEl.value : '';
      if (promptEl) {{
        try {{ const sp = localStorage.getItem(PROMPT_KEY); if (sp !== null) promptEl.value = sp; }} catch (e) {{}}
        promptEl.addEventListener('input', () => {{ try {{ localStorage.setItem(PROMPT_KEY, promptEl.value); }} catch (e) {{}} }});
      }}
      if (resetBtn && promptEl) {{
        resetBtn.addEventListener('click', () => {{
          promptEl.value = defaultPrompt;
          try {{ localStorage.setItem(PROMPT_KEY, defaultPrompt); }} catch (e) {{}}
        }});
      }}

      async function loadModels(agent, preferred) {{
        modelSel.innerHTML = '<option value="">— loading models —</option>';
        if (!agent) {{ modelSel.innerHTML = '<option value="">— select a worker first —</option>'; return; }}
        try {{
          const r = await fetch('/api/council/valid-models?agent=' + encodeURIComponent(agent), {{ cache: 'no-store' }});
          const d = await r.json();
          const models = (d && d.ok && Array.isArray(d.models)) ? d.models : [];
          if (!models.length) {{ modelSel.innerHTML = '<option value="">(no models)</option>'; return; }}
          modelSel.innerHTML = '<option value="">— select a model —</option>' +
            models.map(m => {{
              const mv = (typeof m === 'string') ? m : (m.model || '');
              const pv = (typeof m === 'string') ? '' : (m.provider || '');
              return '<option value="' + escapeHtml(mv) + '" data-provider="' + escapeHtml(pv) + '">' + escapeHtml(mv) + '</option>';
            }}).join('');
          if (preferred) modelSel.value = preferred;
        }} catch (e) {{
          modelSel.innerHTML = '<option value="">(failed to load models)</option>';
        }}
      }}

      try {{ const sa = localStorage.getItem(AGENT_KEY); if (sa) sel.value = sa; }} catch (e) {{}}
      let savedModel = ''; try {{ savedModel = localStorage.getItem(MODEL_KEY) || ''; }} catch (e) {{}}
      if (sel.value) loadModels(sel.value, savedModel);
      sel.addEventListener('change', () => {{
        try {{ localStorage.setItem(AGENT_KEY, sel.value); }} catch (e) {{}}
        loadModels(sel.value, '');
      }});
      modelSel.addEventListener('change', () => {{ try {{ localStorage.setItem(MODEL_KEY, modelSel.value); }} catch (e) {{}} }});
      btn.addEventListener('click', async () => {{
        const agent = sel.value;
        const model = modelSel.value;
        const provider = modelSel.options[modelSel.selectedIndex] ? (modelSel.options[modelSel.selectedIndex].getAttribute('data-provider') || '') : '';
        if (!agent) {{ meta.textContent = 'Select a worker first.'; return; }}
        if (!model) {{ meta.textContent = 'Worker selected — pick a model next.'; return; }}
        btn.disabled = true;
        const t0 = Date.now();
        meta.textContent = agent + ' (' + model + ') narrating…';
        out.textContent = '';
        try {{
          const customPrompt = promptEl ? promptEl.value.trim() : '';
          const resp = await fetch('/api/council/commentary', {{
            method: 'POST', headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ agent: agent, model: model, provider: provider, prompt: customPrompt, events: 14 }})
          }});
          const d = await resp.json();
          const secs = ((Date.now() - t0) / 1000).toFixed(1);
          if (d.ok) {{
            meta.textContent = '🎙️ ' + agent + ' · ' + (d.model || model) + ' · ' + (d.generated_at || '') + ' · ' + secs + 's';
            renderNarration(d.commentary || '(empty)');
          }} else {{
            meta.textContent = 'Error (' + secs + 's)';
            out.textContent = (d.error_name ? d.error_name + ': ' : '') + (d.error || 'commentary failed');
          }}
        }} catch (err) {{
          meta.textContent = 'Connection error';
          out.textContent = String(err);
        }} finally {{
          btn.disabled = false;
        }}
      }});
    }}
  </script>
</head>
<body>
  <header>
    <div class="header-row">
      <h1>Council Dashboard</h1>
      <div style="display:flex;align-items:center;gap:8px">
        <a href="/council/glass" title="Open Liquid Glass theme (secondary)" style="align-self:center; text-decoration:none; color:var(--accent); border:1px solid var(--border); border-radius:6px; padding:6px 10px; font-size:12px; font-weight:600; background:var(--panel);">&#129695; Glass</a>
        <button id="clear-council-session" class="clear-session-button" type="button">Clear session</button>
      </div>
    </div>
  </header>
  <section class="broker">
    <form id="broker-form" class="broker-row">
      <div class="composer">
        <textarea id="broker-text" rows="3" data-scroll-chain="" autocomplete="off" title="Enter to send — Shift+Enter for newline" placeholder="Enter to send — Shift+Enter for newline"></textarea>
        <div class="composer-toolbar">
     <div class="picker-cluster" role="group" aria-label="Worker and model">
     <select id="broker-to" class="toolbar-select" aria-label="Target agent">
       {''.join(f'<option value="{esc(a)}"{" disabled" if not srv._council_agent_caps(a).get("enabled", True) else ""}>{esc(srv._COUNCIL_AGENT_LABELS.get(a, a))}{"" if srv._council_agent_caps(a).get("enabled", True) else " (disabled)"}</option>' for a in srv._council_dashboard_agents())}
     </select>
     <select id="broker-model" class="toolbar-select broker-model-select" aria-label="Model for next dispatch" title="Model applies to next dispatch" hidden></select>
     </div>
     <span id="broker-model-status" class="broker-model-status"></span>
   </div>
        <button id="broker-send" class="composer-send" type="submit" aria-label="Send" title="Send">
          <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
        </button>
      </div>
    </form>
    <div id="broker-running" class="broker-running"></div>
    <div id="broker-events" class="broker-events" aria-live="polite"></div>
    <button id="broker-scroll-bottom" class="broker-scroll-bottom" type="button" data-visible="0" aria-hidden="true" aria-label="Scroll transcript to latest" title="Scroll to latest">
      <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12l7 7 7-7"/></svg>
    </button>
    <div id="broker-resize" class="broker-resize" title="Drag to resize"></div>
  </section>
  <details id="cron-panel" class="cron-panel">
    <summary><span>Council schedules</span><span class="meta">SQLite · overlap-safe · UTC</span></summary>
    <div class="cron-grid">
      <section class="cron-editor">
        <form id="cron-form" class="cron-form">
          <input id="cron-id" type="hidden">
          <label>Name<input id="cron-name" required maxlength="100" placeholder="Daily repository audit"></label>
          <label>Worker<select id="cron-worker" required>{''.join(f'<option value="{esc(a)}">{esc(srv._COUNCIL_AGENT_LABELS.get(a,a))}</option>' for a in srv._council_dashboard_agents())}</select></label>
          <label>Worker model<select id="cron-worker-model" required><option value="">— select worker first —</option></select></label>
          <label>Schedule type<select id="cron-schedule-type"><option value="interval">Interval</option><option value="cron">5-field cron (UTC)</option></select></label>
          <label>Schedule value<input id="cron-schedule-value" required value="1h" placeholder="15m or 0 9 * * 1-5"></label>
          <label>Timeout (seconds)<input id="cron-timeout" type="number" min="30" max="7200" value="3600"></label>
          <label class="cron-check"><input id="cron-enabled" type="checkbox" checked> Enabled</label>
          <label class="wide">Worker prompt<textarea id="cron-prompt" rows="4" maxlength="100000" required placeholder="Self-contained task for the selected worker"></textarea></label>
          <label class="cron-check wide"><input id="cron-narrator-enabled" type="checkbox"> Narrate after successful worker completion</label>
          <label>Narrator worker<select id="cron-narrator-worker">{''.join(f'<option value="{esc(a)}">{esc(srv._COUNCIL_AGENT_LABELS.get(a,a))}</option>' for a in srv._council_dashboard_agents())}</select></label>
          <label>Narrator model<select id="cron-narrator-model"><option value="">— select worker first —</option></select></label>
          <label class="wide">Narrator prompt<textarea id="cron-narrator-prompt" rows="2" maxlength="4000" placeholder="Optional; dashboard narrator default is used when blank"></textarea></label>
          <div class="wide cron-actions"><button class="primary" type="submit">Save job</button><button id="cron-reset" type="button">New</button><span id="cron-status" class="cron-status" aria-live="polite"></span></div>
        </form>
      </section>
      <section class="cron-list-wrap">
        <div class="cron-subhead"><span>Jobs</span><button id="cron-refresh" type="button">Refresh</button></div>
        <div id="cron-list" class="cron-list"><div class="cron-empty">Loading jobs…</div></div>
        <div class="cron-subhead cron-history-head"><span>Recent runs</span></div>
        <div id="cron-runs" class="cron-runs"><div class="cron-empty">Loading history…</div></div>
      </section>
    </div>
  </details>
  <div id="agent-vis-bar" role="toolbar" aria-label="Show or hide agent cards">
    <span class="vis-label">Agents</span>
  </div>
  <main>
    {''.join(cards)}
  </main>
  <button id="narrator-toggle" class="narrator-toggle" type="button" aria-controls="narrator-panel" aria-expanded="true" aria-label="Hide Narrator" title="Hide Narrator">−</button>
  <aside id="narrator-panel" class="narrator-panel" aria-label="Council narrator" aria-hidden="false">
    <div id="narrator-resize" class="narrator-resize" role="separator" aria-orientation="vertical" title="Drag to resize narrator width"></div>
    <div class="narrator-head"><span class="narr-title">🎙️ Narrator</span><button id="narrator-settings-toggle" class="narrator-settings-toggle" type="button" aria-controls="narrator-settings" aria-expanded="true" aria-label="Hide narrator settings" title="Hide narrator settings">▲</button></div>
    <div class="narrator-body">
      <div id="narrator-settings" class="narrator-settings">
      <label class="narrator-label">Narrator worker</label>
      <select id="narrator-agent" class="toolbar-select" aria-label="Select narrator worker">
        <option value="">— select a registered worker —</option>
        {''.join(f'<option value="{esc(a)}"{" disabled" if not srv._council_agent_caps(a).get("enabled", True) else ""}>{esc(srv._COUNCIL_AGENT_LABELS.get(a, a))}</option>' for a in srv._council_dashboard_agents())}
      </select>
      <label class="narrator-label">Model</label>
      <select id="narrator-model" class="toolbar-select" aria-label="Select narrator model">
        <option value="">— select a worker first —</option>
      </select>
      <div class="narrator-prompt-row">
        <label class="narrator-label">Narrator prompt</label>
        <button id="narrator-prompt-reset" type="button" class="narrator-reset" title="Restore default prompt">reset</button>
      </div>
      <textarea id="narrator-prompt" class="narrator-prompt" rows="5" aria-label="Narrator prompt (custom)" placeholder="Enter your narrator prompt…">{esc(srv._COUNCIL_NARRATOR_DEFAULT_PROMPT)}</textarea>
      <button id="narrator-btn" type="button" class="narrator-btn">Commentary</button>
      <div id="narrator-meta" class="narrator-meta"></div>
      </div>
      <div id="narrator-out" class="narrator-out">Select a worker + model, then press <b>Commentary</b> for a live council narration.</div>
    </div>
  </aside>
</body>
</html>"""

    return html
