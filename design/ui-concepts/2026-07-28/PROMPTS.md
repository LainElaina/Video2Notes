# ImageGen Prompt Set

执行方式：Codex built-in `image_gen`，每个方向单独调用一次。所有图片都是全新生成；没有把当前 Video2Notes 截图作为输入。

## 统一基础提示

```text
Use case: ui-mockup
Asset type: shippable high-fidelity Windows desktop React/Tauri application design reference
Primary request: create ONE single complete UI screenshot for “Video2Notes”, a local AI application that converts videos into evidence-backed notes. This is a real product UI reference, not concept art, not a marketing page, and not a montage.
Scene/backdrop: a complete 16:10 desktop application window, approximately 1440×900, edge-to-edge with no laptop/device frame and no surrounding room.
Subject and information architecture: an active Reader workspace. Include compact main navigation with “New task”, “Running”, “Notes”, “Models”; a video preview paused at “08:42”; a structured Markdown-like note with concise contents and embedded keyframe; an evidence timeline aligned to the video with “Speech”, “Screen text”, and “Visual change”; a mode control “Fast”, “Balanced”, “Accurate”; and an “Export” action.
Composition/framing: practical desktop-native split panes with precise spacing and accessible hierarchy. The note must remain a continuous readable surface. The same functional baseline may be recomposed to fit the visual direction.
Constraints: realistic implementable React UI; sparse and legible English labels; no generic SaaS dashboard; no marketing hero; no card sea or cards inside cards; no huge title; no fantasy/game HUD; no people; no illustration; no third-party logos; no watermark; no device mockup; no extra windows; show only one UI design.
```

## 01 · Pearl Glass

```text
Visual direction: bright pearl white and ice gray; extremely restrained local translucency and blur; teal status color and coral time cursor; hairline borders and calm negative space; premium Swiss/Apple productivity feel. Do not turn every region into a glass card.
```

## 02 · Obsidian Cinema

```text
Visual direction: dark obsidian and graphite, video-first professional workspace, warm white text, electric blue status and orange playhead; cinematic yet readable; hierarchy through tonal surfaces rather than heavy shadows.
```

## 03 · Precision Monochrome

```text
Visual direction: Linear/Vercel-like precision in black, white, and gray, with acid green only for running state; 1px grid, sharp typography, compact radii, command-palette efficiency; minimal without becoming empty.
```

## 04 · Warm Editorial

```text
Visual direction: warm ivory paper, black ink, sage and rust accents; premium magazine/research editor; serif section titles with sans-serif controls; evidence behaves like editorial annotation; flat columns, almost no shadow, reading first.
```

## 05 · Spatial Command

```text
Visual direction: midnight blue-black, floating command bar and collapsible docks, Raycast/Arc-inspired spatial depth for a professional media analysis tool; restrained violet-blue and mint glow; very few controls, fluid and premium.
```

## 06 · Fluent Mica

```text
Visual direction: modern Windows 11 Fluent 2 and Mica, soft cloud-gray background, translucent title bar, rounded split panes, system-native iconography, clear focus states, blue emphasis; feel like an excellent native Windows creative tool rather than a Settings clone.
```

## 07 · Swiss Signal

```text
Visual direction: high-contrast white, deep ink, international typographic grid, cobalt blue primary and signal-red playhead; strong rules, large time codes, no shadows; contemporary information design studio.
```

## 08 · Soft Brutalist

```text
Visual direction: modern soft neo-brutalism for daily productivity, cream gray, coal-black structural borders, sparse fluorescent orange and lime, large direct controls, asymmetric layout, very little rounding; bold but highly readable.
```

## 09 · Calm Bento

```text
Visual direction: quiet bone white and pale gray, graphite text, sage and pale apricot accents; a small number of large bento regions rather than nested cards; 16–20px soft radii; balanced productivity density and breathing room.
```

## 10 · Data Studio

```text
Visual direction: expert high-density research/developer station, deep graphite with green/cyan status and amber warnings; visible waveform, PTS ticks, stage telemetry, evidence filters and performance indicators; premium IDE plus video QA workstation, dense but exceptionally ordered.
```

## 11 · Aurora Luminous

```text
Visual direction: dark indigo with restrained low-saturation cyan/violet aurora light, translucency only on the primary layer, high-contrast reading area, softly luminous playhead and progress; futuristic but not a game HUD or neon spectacle.
```

## 12 · Japanese Quiet

```text
Visual direction: Japanese editorial restraint, warm white, charcoal, pale gray-blue and one vermilion accent; fine rules, careful whitespace, compact icons, modest serif titles and sans-serif tool labels; calm, precise, no decorative motifs.
```

## 13 · Material Expressive

```text
Visual direction: contemporary Material Expressive for a serious desktop creative tool, warm pearl-gray canvas, deep aubergine text, restrained coral and electric violet, a few confident variable-radius action shapes, editorial typography and subtle tonal elevation; approachable but expert.
```

## 14 · Immersive Timeline

```text
Visual direction: immersive timeline-first expert workspace, blue-black frame, warm near-white reading surface, cyan Speech, amber Screen text, magenta Visual change, bright coral 08:42 playhead. A multilayer horizontal PTS timeline spans beneath a large video canvas; a calm resizable note panel sits beside it. Minimal chrome, fluid split panes, cinematic but focused.
```

