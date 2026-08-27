# Device Center pixel cascade, and the shader route we did not take

Date: 2026-08-27

## Outcome

The alert callout's leading edge is a **pixel cascade**: a short grid of small
quads whose per-cell opacity is driven by a staggered phase wave, so a crest of
light travels left to right and the cells behind it decay back to a dim rest
state.

It is built from ordinary GPUI elements. It is not a shader, and this note
records why, what the shader version would be, and what it would cost — so the
decision can be revisited deliberately rather than rediscovered.

## Why not a shader today

GPUI 0.2.2 from crates.io exposes **no application-level shader hook**. There is
no custom scene primitive, no post-process pass, and no way for app code to add
a fragment program. Checked against the pinned source at
`gpui-0.2.2/src`:

| Symbol | Occurrences in gpui 0.2.2 |
| --- | --- |
| `EdgeFade` | 0 |
| `BackdropBlur` | 0 |
| `with_edge_fade` | 0 |

There is also **no wgpu**. GPUI compiles per-platform backends with per-platform
shading languages:

- Windows — Direct3D, HLSL (`platform/windows/directx_renderer.rs`,
  `platform/windows/shaders.hlsl`, compiled by `fxc.exe`);
- macOS — Metal (`platform/mac/metal_renderer.rs`, `shaders.metal`);
- Linux — Blade/Vulkan.

So "add a wgpu shader" is not a shape this codebase has. A custom effect means
editing the renderer of a GPUI **fork**, once per backend, and rebasing that
fork on every upstream bump. Inari builds Device Center on all three platforms
in CI, so a fork is a three-backend commitment, not one.

## The reference implementation

[Comet](https://github.com/zeronsh/comet) (zeron.sh) is the highest-signal
example of this done properly in GPUI. It does exactly what the shader route
would require: it pins a GPUI fork and adds renderer primitives.

```toml
gpui = { git = "https://github.com/wingleeio/zed", rev = "e2ddcc68" }
```

Its own manifest comment records what that revision carries:

- **`BackdropBlur`** (`a6c1ad5`) — a scene primitive plus a blur pass, giving
  true within-window frosted glass, and destination-alpha compositing fixes for
  transparent windows.
- **`EdgeFade`** (`14baea0`, extended by `5d1f83d`) — `Window::with_edge_fade`,
  a scope that fades primitives by their per-pixel distance to an edge. The
  horizontal variant covers quads and images.

Both are consumed through custom `Element` implementations that wrap a whole
subtree so it paints inside **one scene layer**:

- [`crates/ui/src/frost.rs`](https://github.com/zeronsh/comet/blob/main/crates/ui/src/frost.rs)
  — `frosted(corner_radius, blur_radius, child)`. Its module doc explains the
  single-layer requirement: with per-primitive bounds-tree ordering, a hover
  repaint elsewhere could reassign the card's quads *below* the blur, and
  washes, dividers and borders would intermittently get blurred away.
- [`crates/ui/src/edge_fade.rs`](https://github.com/zeronsh/comet/blob/main/crates/ui/src/edge_fade.rs)
  — `edge_faded(band, top, bottom, child)`. Its reason for existing is worth
  keeping: over a see-through blurred backdrop **no painted overlay can fade
  content out**, because "what is behind the window" is not a paintable colour.
  That is the one class of effect the element route genuinely cannot fake.

Blur radii it uses: 44 for floating menus and dialogs, 12–16 for the composer
pill.

## The effect we did build, and its name

Comet's own motion catalog names the two relevant per-cell waves:

- **`zeron-pulse`** — 2.4s staggered cell opacity 0.08 → 1, scale 0.9 → 1, used
  by the pixel-grid logo loader;
- **`gradient-spin-pulse`** — a 750ms per-cell phase wave used by the "gradient
  matrix spinner".

The generic technique is a **staggered per-cell phase wave over a pixel grid**.
Comet implements it in
[`crates/ui/src/loaders.rs`](https://github.com/zeronsh/comet/blob/main/crates/ui/src/loaders.rs)
and describes the rendering contract precisely:

> each cell is its own `with_animation` repeating element sharing one period;
> per-cell offsets come from `motion::staggered_phase`, so all cells stay
> phase-locked (they start on the same frame) without a shared clock. Cells
> animate inside fixed-size slots — opacity and inner size are paint-local and
> never move surrounding layout.

The math, from `crates/proto/src/motion.rs`:

```rust
fn staggered_phase(raw_delta: f32, index: usize, stagger: f32) -> f32 {
    (raw_delta - index as f32 * stagger).rem_euclid(1.0)
}

fn pulse_wave(phase: f32) -> f32 {
    0.5 - 0.5 * (phase * TAU).cos()
}
```

That is what Device Center's cascade uses. Element-built, no fork, and it is a
grid of real quads rather than a simulation of one.

## What the shader route would buy

Worth taking only if one of these becomes a requirement:

1. **Fading content over a transparent backdrop.** The `EdgeFade` case above.
   Unfakeable with elements.
2. **Within-window frosted glass.** Floating surfaces that blur the app content
   behind them, rather than approximating with tonal washes.
3. **Per-pixel glow falloff and dithering.** An element grid quantises to its
   cell size; a fragment program does not.
4. **Cell counts where quads stop being cheap.** A 40 x 12 px cascade is a few
   dozen quads. A full-surface effect is not.

## What it would cost

- A GPUI fork owned by this project, with HLSL and Metal variants of the pass
  and a Blade path for Linux.
- A rebase on every GPUI bump, against a pre-1.0 upstream.
- Shader compilation in the release build. Device Center already depends on
  `fxc.exe` for gpui's own HLSL, which `deploy/windows/build.ps1` now resolves
  from the installed Windows kits; a custom pass adds more of that surface.

The existing glass research reached the same conclusion from the other
direction and should be read with this note: *review and upstream the minimum
renderer change rather than taking Comet's whole fork without an ownership
plan*. See [the glass research](device-center-glass-research.md).

## Sources

- Comet — <https://github.com/zeronsh/comet> (zeron.sh), the GPUI fork pin in
  its `Cargo.toml`, and `crates/ui/src/{frost,edge_fade,loaders,motion}.rs`.
- The GPUI fork — <https://github.com/wingleeio/zed> at `e2ddcc68`, commits
  `a6c1ad5` (BackdropBlur), `14baea0` and `5d1f83d` (EdgeFade).
- gpui 0.2.2 source, as pinned in this workspace's `Cargo.lock`.
- `gpui-starter` — <https://github.com/lassejlv/gpui-starter>, a minimal
  reference for GPUI app scaffolding.
