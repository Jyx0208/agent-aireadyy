# Primary-theme ONLINE align closeout
Room: primary-theme-online
Provider: ALL pi/relay/grok-4.5

## User requirements (must verify)
1. Search strategy = primary theme DEEP (e.g. immunopeptidomics), species/DDA as post-filters NOT equal shallow seeds
2. 瓒婂瓒婂ソ = NO business candidate-pool cap (safety ceiling only, ~20000)
3. Every ~30 verified usable projects emit partial L1 batch file for operator to process
4. Stop button works during discovery

## Files
- src/agent/discovery/query_builder.py, query_portfolio.py, search_environment.py
- src/agent/control_plane/discovery.py (_maybe_emit_partial_l1_delivery)
- src/agent/web/app.py (maximize pool 20000, partial_delivery_batch_size)
- frontend grill-tree.ts (toDiscoveryJobPayload open-ended pool 20000)
- DiscoveryProgressMessage 鍋滄鍙戠幇 + CarbonAgentChat cancelDiscoveryJob

## Roles
- PT-S supervisor: run pytest + smoke; ACCEPT/REJECT
- PT-T tests: pytest primary_theme + discovery search env
- PT-FE: npm build frontend static
