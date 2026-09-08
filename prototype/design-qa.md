# Signal Tracking Design QA

- Source visual truth: `C:\Users\13001\.codex\generated_images\019fd5bd-3c16-7153-836d-f683049d34a0\exec-547a2c1e-3ffa-45ee-bf35-a654e4f28ade.png`
- Implementation screenshot: `C:\Users\13001\.codex\visualizations\2026\08\06\019fd5bd-3c16-7153-836d-f683049d34a0\signal-monitor-final-1440x1024-v2.png`
- Full comparison: `C:\Users\13001\.codex\visualizations\2026\08\06\019fd5bd-3c16-7153-836d-f683049d34a0\signal-monitor-comparison-final.png`
- Focused detail comparison: `C:\Users\13001\.codex\visualizations\2026\08\06\019fd5bd-3c16-7153-836d-f683049d34a0\signal-monitor-comparison-detail-final.png`
- State: signal monitor loaded, ETHUSDT row expanded, market execution and fixed-USDT sizing selected.
- Browser: Codex in-app browser.
- Viewport: 1440 x 1024 CSS pixels, device pixel ratio 1, implementation capture 1440 x 1024 pixels.
- Density normalization: source was 1487 x 1058 pixels and was scaled to 1440 x 1024 before side-by-side comparison.

## Full-view comparison evidence

- Information architecture matches the selected monitor direction: fixed sidebar, compact header, five-part settings band, status filters, table header, expanded signal detail, remaining signal rows, and pagination.
- The settings band uses the same true-white background, light-gray borders, blue selected controls, compact labels, and Telegram source treatment.
- The main table preserves the source column order, row density, direction colors, parsing state, execution state, sizing, and timing hierarchy.
- The implementation intentionally shows `自动执行未启用` and `待执行` because those are the current backend states. The source mock's green enabled state is not copied when it would misrepresent execution safety.
- The implementation intentionally uses six available/demo rows rather than the mock's count of 36; structure and density remain faithful.

## Focused region comparison evidence

- Original Telegram messages, message numbers, Telegram icons, timestamps, linked follow-up copy, parsed fields, and automatic execution result all align in three columns.
- The final pass added signal type, parsing time, quantity, amount, order ID, and execution time so the right two panels have the same anatomy and vertical rhythm as the selected source.
- No raster assets were required. All visible imagery is standard UI iconography from the existing Phosphor icon library; there are no placeholder images, CSS illustrations, custom SVGs, or degraded source assets.

## Required fidelity surfaces

- Fonts and typography: Noto Sans SC family and the existing 400/500/600/700 weights are preserved. Heading, labels, table text, small metadata, truncation, and optical hierarchy match the compact source.
- Spacing and layout rhythm: 190px sidebar, 24–28px workspace gutter, 158px settings band, table tracks, three-column detail, borders, and small radii match. Expanded detail height was increased during iteration.
- Colors and visual tokens: true white, `#1769f6` blue, light gray borders, green success, red short, and amber waiting states match the source without gradients or extra elevation.
- Image quality and asset fidelity: no custom raster assets exist in the selected mock; icons remain sharp code-native library assets at all checked sizes.
- Copy and content: manual-review copy and the old receive/parse/risk/wait flow are absent. Channel, execution method, fixed USDT, 0%–10% position sizing, merged Telegram messages, parsed result, and automatic result copy are present.

## Interaction and browser checks

- Switching to position sizing enables the numeric and range controls.
- Entering 12% is clamped to the permitted maximum of 10%.
- Filters return 5 executed signals, 1 pending signal, and a real empty state for zero-count filters.
- Expanding BTCUSDT replaces the open detail with the selected signal's detail.
- At 390 x 844, the page has no document-level horizontal overflow; the wide data table scrolls inside its own container. The save button expands to 351px and no longer wraps vertically.
- No Vite/framework overlay appeared and the final clean-tab console contained no errors or warnings.

## Comparison history

1. P2: expanded detail was too compressed and status tabs were static. Fixed by increasing the detail height and implementing real filters plus an empty state.
2. P2: mobile save button wrapped vertically. Fixed by stacking the mobile header and making the button full width.
3. P2: execution detail omitted quantity, amount, signal type, parsing time, and execution time. Fixed by adding the missing rows and repeating the focused comparison.

## Remaining differences

- Accepted intentional deviation: live disabled/pending status replaces the source mock's enabled/executed status until the user explicitly enables and saves automatic execution.
- Accepted intentional deviation: row totals reflect the available prototype data rather than the source mock's illustrative totals.

No actionable P0, P1, or P2 findings remain.

final result: passed
