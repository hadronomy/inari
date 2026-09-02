# The Device Center development environment

Research and design for two things a GPUI application needs and this one does
not have: a **Bench** where every component can be explored across its real
states, and **devtools** that explain what the running application is actually
doing.

Research date: 2026-09-01. Sources are the checkouts on disk, not memory:

- our GPUI fork at `/Volumes/Yggdrasil/Projects/zed-fork`, rev `4e45894`, which
  is what `[patch.crates-io]` resolves to. File:line references to `crates/gpui`
  below are from that tree, so they describe the code we compile.
- upstream Zed at `~/gpui-research/zed-src/zed`, rev `797e5dc9`, for the parts
  the fork does not carry.
- `gpui-component 0.5.1` from the registry, which we already depend on.
- `~/gpui-research/GPUI-POLISH-RESEARCH.md` for the surrounding capability map.
- DialKit (joshpuckett/dialkit) and Storybook 9 for the external workflow.

---

## 1. What already exists, and why nobody uses it

### 1.1 GPUI ships a complete element inspector

This was the largest surprise. `crates/gpui/src/inspector.rs` is not a stub. It
carries a full picking model:

- `InspectorElementId { path: Rc<InspectorElementPath>, instance_id }`, where
  the path is the nearest ancestor's `GlobalElementId` plus the
  `&'static panic::Location` where the element was constructed
  (`inspector.rs:32-42`). Two identical rows from one `for` loop stay apart
  through `instance_id`.
- Every element reports `source_location()` (`element.rs:69`). `div()`,
  `img()` and `svg()` are `#[track_caller]`, so the location is the call site
  in *our* code, not inside GPUI.
- Picking. `Interactivity::should_insert_hitbox` ends with
  `|| window.is_inspector_picking(cx)` (`div.rs:1711`), so while picking is on
  **every** div gets a hitbox even if it has no listeners. Hover selects, click
  pins, and the scroll wheel walks *up the ancestor stack* through
  `pick_depth` (`window.rs:4650-4681`) — the equivalent of the browser's
  breadcrumb bar, but driven by the wheel.
- Typed per-element state. `Window::with_inspector_state<T>` (`window.rs:4544`)
  lets any element hand a value to the inspector; `Div` hands over
  `DivInspectorState { base_style, bounds, content_size }` (`div.rs:1269-1279`)
  and — this is the part that matters — **reads it back** at the top of
  `request_layout` (`div.rs:1546-1560`). Writing to that state re-styles the
  live element.
- Two registration points on `App`: `set_inspector_renderer` and
  `register_inspector_element::<T>` (`app.rs:2085-2096`).

Everything is behind `#[cfg(any(feature = "inspector", debug_assertions))]`, so
a release build carries none of it.

### 1.2 …and `gpui-component` already wires it up in our application

`gpui_component::init` calls `inspector::init` (`lib.rs:100-101`), which binds
`ctrl-shift-i` (`cmd-alt-i` on macOS), registers a `DivInspectorState` renderer,
and sets the inspector renderer. We call `gpui_component::init` in `main`.

**So the Device Center has had a working element inspector all along.** Verified
on the Windows desktop against the running dev build: `ctrl-shift-i` opens a
docked panel showing the source location, origin, size, content size, a live
**Rust** style editor (`fn build() -> Div { div().h_full().flex_1() }`) with
completions driven by `styled_reflection`, and a live JSON style editor. Edits
apply to the running element.

Nothing in the application points at it. No menu item, no affordance, no
mention in `AGENTS.md`. That is the real defect: the tool is good and invisible.

Its limits, fairly stated:

- it paints in `gpui-component`'s own palette, so it reads as a foreign object
  beside the Inari surfaces;
- it shows one element, never the tree around it;
- it shows numbers, never geometry — no box model, no spacing bands, no hitbox;
- it has no notion of frames, cost, or repaint.

### 1.3 The dock is 30rem, reserved, and not negotiable

`Window::draw` subtracts a fixed `rems(30.0)` from the viewport whenever an
inspector exists (`window.rs:2019-2031`), then prepaints the inspector into that
strip (`window.rs:4582-4596`). The width is hardcoded.

This is a constraint worth accepting rather than forking around. Browser
devtools dock the same way, the application never gets occluded, and the layout
change is honest feedback about how the UI responds to width. It also decides
the shape of everything below: **there is exactly one panel, and GPUI owns
where it goes.**

### 1.4 `DebugBelow` — outline every element, with no instrumentation

`Style::paint` checks `cx.has_global::<DebugBelow>()` and paints a red outline
around every div when the global is set (`style.rs:612-618`, and the hovered
element's `GlobalElementId` is drawn as text at `div.rs:1902-1915`). Setting one
global turns on outlines application-wide. No component needs to know.

### 1.5 Zed's two Story systems, and which one to learn from

Zed has both, and they disagree:

- `crates/storybook` — a **separate binary**. Every story must be added to the
  `ComponentStory` enum *and* to a `match` arm in `story(...)`
  (`story_selector.rs:13-70`). Two hand-kept lists, and the stories cannot see
  the real application.
- `crates/component` — the replacement. A `Component` trait with `preview()`,
  `scope()`, `status()`, `description()`, registered through `inventory` so a
  component registers **itself** from its own file (`component.rs:26-59`), with
  the gallery rendered **inside the real workspace**.

Our `dev_tools.rs` is structurally the first one: a `Page` enum, an `ALL` array,
a `rail_item` match, a `render` match, and eight `Show*` actions — four lists
to keep in step for one new page. The user has already rejected this shape once,
in the shader parameter work. It should not survive here either.

### 1.6 DialKit: the parameter model worth stealing

DialKit is a floating control panel that binds live values to sliders, toggles,
colour pickers and action buttons. Two of its ideas carry over:

- **The declaration site is the read site.** `useDialKit(name, config)` returns
  the current values. There is no separate schema to keep in step with the code
  that consumes it.
- **Control type is inferred, not declared.** `0.5` becomes a 0..1 slider,
  `[24, 0, 100]` a ranged one, a bool a toggle, a nested object a folder.

The first idea is the important one and it translates to Rust exactly. The
second translates through the type system instead of through value shapes.

### 1.7 Storybook 9: what to take and what to leave

Take: the story as the unit of both development and testing; a component
explored across *states*, not just rendered once; the catalog as the primary
navigation.

Leave: the addon architecture (we do not have third parties), the docs mode
(rustdoc already exists), and — for now — visual regression, which is a project
of its own and is listed in §6 with what it needs.

---

## 2. Do the two systems share infrastructure?

Yes, and the sharing is not cosmetic. Once the Bench is a window like any
other, the inspector works inside it for free, because inspection is a
**window** facility, not an application one. That single fact collapses most of
the apparent duplication:

| Concern | Bench | Application window |
|---|---|---|
| Panel host | GPUI inspector dock | GPUI inspector dock |
| Tool set | the same | the same |
| Selection model | `InspectorElementId` | `InspectorElementId` |
| Overlay painting | the same | the same |
| Appearance / material / motion controls | the same | the same |
| Catalog rail | yes | no |
| Knobs | populated | empty |

So the shared object is **the Panel and its tools**. The Bench adds a catalog
and a stage; the application window adds nothing at all. There is no second
inspection model, no second overlay renderer, and no second control vocabulary.

---

## 3. Architecture

Seven words, one meaning each:

- **Story** — one registered preview of one component or screen.
- **Bench** — the window that lists stories and renders the selected one.
- **Stage** — the framed area of the Bench where a story renders.
- **Panel** — the docked tool surface, hosted by the GPUI inspector dock.
- **Tool** — one tab of the Panel.
- **Bubble** — the floating launcher in the application window.
- **Dial** — the object a story reads its live parameters from.
- **Knob** — one such parameter.

### 3.1 The Dial: knobs where they are read

A story receives a `&mut Dial` and reads parameters from it:

```rust
let radius = dial.range("Radius", 12.0, 0.0..=32.0);
let disabled = dial.flag("Disabled", false);
let emphasis = dial.pick("Emphasis", Emphasis::Normal);
if dial.press("Replay") { /* restart the transition */ }
```

Each call does three things at once: it declares the knob, gives it a default,
and returns its current value. The Panel renders the knobs **that this frame's
render actually read**, in the order it read them.

This is worth being explicit about, because it is the whole ergonomic argument:

- adding a knob is one line, at the only place that cares;
- a knob cannot drift from its use, because there is no second declaration;
- deleting the read deletes the knob;
- conditional knobs work — a knob read only when a flag is on appears only
  when the flag is on;
- "reset" is free, because the default is in the call.

The frame ordering makes it exact rather than approximate. `Window::draw`
prepaints the root before the inspector (`window.rs:2035-2042`), so by the time
the Panel renders, this frame's schema is already recorded.

Storage is a `Global` keyed by `(story id, label)`. Types are inferred from the
method, not from the value:

| Call | Control |
|---|---|
| `flag(label, bool)` | switch |
| `range(label, f32, RangeInclusive<f32>)` | slider with a numeric readout |
| `count(label, usize, RangeInclusive<usize>)` | stepper |
| `text(label, &str)` | text field |
| `pick::<T: Choice>(label, T)` | segmented control |
| `press(label)` | button; true for one frame |
| `group(title)` | a heading in the Panel |

`Choice` is one associated const beside the type it describes:

```rust
impl Choice for Emphasis {
    const VARIANTS: &'static [(Self, &'static str)] = &[
        (Self::Normal, "Normal"),
        (Self::Primary, "Primary"),
        (Self::Ghost, "Ghost"),
    ];
}
```

One list, next to the enum. This is deliberately *not* the `storybook` shape,
where the list of things lives away from the things.

### 3.2 Stories register themselves

`inventory` (already in `Cargo.lock` at 0.3.24) collects stories from wherever
they are written. A story lives beside its component, the way tests do:

```rust
dev::story! {
    id: "control.button",
    name: "Button",
    scope: Scope::Controls,
    about: "Every emphasis, with the reporting swap.",
    render: |dial, window, cx| { ... },
}
```

No enum, no `ALL`, no match arm, no action per page. The catalog is whatever
was compiled in.

### 3.3 The Panel and its tools

The Panel is a header of tool tabs plus the selected tool's content. It is
returned from `App::set_inspector_renderer`, so GPUI docks it. The same Panel
appears in the Bench and in the application window; the tool list does not
change, only what the tools have to say.

Tools in the first version:

- **Knobs** — the current story's dials. Empty outside the Bench.
- **Element** — the selected element. Hosts `gpui_component::DivInspector`
  (it is `pub`, with `pub fn new` and `pub fn update_inspected_element`), so the
  live Rust and JSON style editors are kept rather than rewritten, and adds our
  own report above it: source location, instance, bounds, content size, and the
  box model read from `base_style`.
- **Layout** — outlines, the box-model overlay, and the size chip.
- **Frames** — render cadence.
- **Stage** — appearance, material, reduced motion, rem size, stage width.

`Tool` is a small trait, and it earns its place: it is the thing that makes
"add a devtool" a single file rather than an edit in four places.

### 3.4 The Bubble

A small floating pill in the application window, dev builds only. It exists
because §1.2 found a good tool nobody could find.

- painted through `deferred()`, so it escapes every content mask and paints
  above the whole tree, and `.occlude()` so hovers never leak beneath it;
- 45% opacity at rest, full on hover, eased through the existing
  `motion::hover_blend` — present but not shouting;
- draggable, so it never sits on the thing being looked at;
- clicking a glyph opens the dock with that tool selected.

It is added at the root through one dev-only call. Ordinary components stay
free of debugging code.

### 3.5 The overlay

The Layout tool paints over the window, not into it:

- **Outline everything** sets `DebugBelow` (§1.4) — one global, no
  instrumentation.
- **The selected element** is drawn properly: the bounds box, the border band,
  the padding band and the content box, with a size chip. The geometry comes
  from the `DivInspectorState` the Element tool already receives, cached into a
  `Selection` global as the renderer runs.

Margin is reported as a number but **not painted**. GPUI resolves margin during
layout and no margin rectangle survives to paint time; drawing a guessed one
would be a lie in the one tool whose whole job is to not lie.

---

## 4. Rejected alternatives

**A floating panel instead of the dock.** DialKit floats, and floating avoids
GPUI's fixed 30rem. Rejected: a floating panel occludes the glass surfaces it
sits on, needs its own drag/resize/z model, and would leave GPUI's dock
reserving 30rem of nothing whenever picking is on. The dock costs one
constraint and removes three problems.

**Widening the fork to make the dock width settable.** We maintain the fork
already, so this was tempting. Rejected for now: it buys layout taste, not
capability, and every fork commit is a rebase liability. If the 30rem strip
turns out to be genuinely wrong in use, it is a ten-line change and this
paragraph is the reason to make it.

**A typed knob struct with a derive**, mirroring `#[derive(Effect)]`. Rejected:
it puts the parameter list somewhere other than the code that reads it, which
is precisely the coupling this design is trying to remove. The derive is right
for shader parameters, because there the struct *is* the GPU buffer layout.
Here there is no second consumer.

**A separate Bench binary**, like `crates/storybook`. Rejected for the reason
Zed itself moved away from it: a separate binary cannot inspect the running
application, cannot share its theme installation, and doubles the startup path.

**Rebuilding the element inspector.** Rejected: `gpui-component`'s is good, it
is already compiled into the binary, and its Rust-style editor with
`styled_reflection` completions is more than we would write. We host it.

---

## 5. What the first version delivers

1. `dev/` replaces `dev_tools.rs`: story registry, Dial, Panel, Bubble, Bench.
2. The eight existing preview pages become registered stories; the `Page` enum,
   its `ALL` array, its two matches and its eight actions are deleted.
3. Component-level stories co-located with `ui/button.rs`, `ui/status.rs`,
   `ui/banner.rs` and `ui/surface.rs`, to prove co-location.
4. Five tools: Knobs, Element, Layout, Frames, Stage.
5. The Bubble in the application window, so the inspector stops being a secret.

---

## 6. DialKit, read properly

Research date: 2026-09-02. Source: `joshpuckett/dialkit` at `1d0ca134`, read
directly — `src/components/Slider.tsx` for the interaction, `src/styles/theme.css`
for the geometry — plus the demo at dialkit.dev.

### 6.1 The row is the whole idea

> Every control is one 36px row with an 8px radius on a five-percent white
> surface. The label sits **inside** the row on the left, the value **inside** it
> on the right, and a slider is that same row with a fill and a handle drawn
> behind them.

A label above its control is what a settings screen does. It doubles the
vertical space and makes twelve knobs unreadable. Putting both inside one row is
why a DialKit panel reads as one instrument rather than as a form, and it is the
single change that matters most.

Tokens (`theme.css:4-45`, `:387-510`):

| Token | Value |
|---|---|
| row height / radius | 36px / 8px |
| surface / hover / active | white at 5% / 10% / 11% |
| border / border hover | white at 10% / 15% |
| label and value | 13px, weight 500, white at 70% |
| value face | mono |
| label inset / value inset | 10px |
| handle | 3 × 20, fully round, `text-primary` |
| hash mark | 1 × 8, fully round, `border-hover`, 200ms fade |
| panel | `#212121`, 14px radius, 20px backdrop blur, 1px border |

### 6.2 The slider, exactly

- **Click versus drag** separate at 3px of pointer travel. A drag tracks the
  pointer exactly. A click asks for a round number: on a span of ten steps or
  fewer it lands on the nearest step, above that it is magnetic to the nearest
  tenth — so clicking the middle of a 0..1 slider gives 0.5, not 0.4913 — and it
  springs there (stiffness 300, damping 25, mass 0.8).
- **Hash marks** are the steps on a coarse span and the tenths on a fine one, so
  the marks and the snapping tell the same story. They fade in only while the
  row is active.
- **Rubber band**: dragging past either end slides the whole track by up to 8px,
  after a 32px dead zone, on `sqrt(min(overflow / 200, 1))` — stiffening as it
  goes — and springs back over 350ms with a little bounce.
- **The handle dodges**. It is invisible at rest, half-lit under the pointer,
  nearly solid while dragging, and drops to a tenth when it would sit under the
  label or the value, squashing to 75% height as it passes. At rest it is a
  quarter of its width, so it reads as a tick rather than a grip.
- **The value is editable in place**: hover it for 800ms and it underlines,
  click and it becomes a text field, Enter commits, Escape cancels.

### 6.3 The full control set

`Slider`, `Toggle`, `SegmentedControl`, `ButtonGroup`, `SelectControl`,
`TextControl`, `ColorControl`, `SpringControl` with a live `SpringVisualization`,
`TransitionControl` with an `EasingVisualization`, `Folder` (nesting),
`Panel` (dragged, tabbed), `PresetManager`, `ShortcutListener` and
`ShortcutsMenu`, and a `DialTimeline` with keyframes. Plus `copy-instruction.ts`,
which copies a panel out as a prompt for a coding agent.

### 6.4 What we have built of it

Landed: the arithmetic, with tests — click snapping, mark placement, band
stretch, handle dodging, decimals from step (`dev/control.rs`).

Not yet built: the row geometry itself, so the panel still stacks a label over
its control; the slider element and its drag; the segmented control and toggle
at DialKit's proportions; select, colour, spring and easing controls; folders,
presets, shortcuts and the timeline. The text field is `gpui_component::Input`
with no chrome, where it should have the focus ring and easing that
`ui/field.rs` gives the enrollment field.

One constraint to record: GPUI 0.2.2 has no spring, so the click-snap will run
on `ui/motion.rs`'s cubic curves rather than DialKit's spring constants. The
shape is close and the vocabulary stays one.

---

## 7. The growth path, and what each step needs

| Tool | What it needs |
|---|---|
| Visual regression | `render_to_image` from `gpui`'s `test-support`, which is not in our feature set, plus a golden-image store and a diff viewer |
| Repaint / invalidation heatmap | renderer instrumentation — a fork change, since `Scene` is `pub(crate)` |
| Frame profiler | per-primitive timing out of the Metal and D3D renderers — a fork change |
| Event and input log | a window-level event tap; GPUI has no public hook |
| Accessibility report | GPUI's `accesskit` surface is not exposed for reading |
| Interaction scenarios | scripted pointer and key events against a story; `VisualTestContext` does this in tests but not in a running window |

Each is a real tool. None of them is blocked on the architecture above, which
is the point of choosing it.

Nor is §6.4's remaining work: the knob model already carries everything a
DialKit-shaped control needs, so the rest is painting and pointer handling.
