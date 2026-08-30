# A GPU effects architecture for GPUI

Date: 2026-08-29.
Question: how do we let Device Center author GPU effects once, in WGSL, and
run them on Metal, Direct3D and Vulkan without writing each effect three
times?

Read [`docs/device-center-glass-research.md`](device-center-glass-research.md)
first. It ends where this document starts: "A custom renderer primitive is
still needed for true within-window frost on floating surfaces."

Sources are the exact code we build against. `gpui 0.2.2` from the registry
root on Yggdrasil, cited below as `gpui-0.2.2/…`; upstream Zed at
`797e5dc95c` in `~/gpui-research/zed-src/zed`, cited as `zed/…`. Line numbers
are from those checkouts.

---

## 1. Outcome in one page

GPUI 0.2.2 has no hook for application GPU work. `Scene` and all eight
primitive types are `pub(crate)` (`gpui-0.2.2/src/scene.rs:24-35`). The only
routes for application pixels are a CPU `RenderImage` and, on macOS only, a
`CVPixelBuffer` surface (`gpui-0.2.2/src/window.rs:3181`). Neither is
acceptable for animated effects.

So the mechanism has to live inside a GPUI fork. This is what every GPUI app
with real effects does. Zeron pins a fork of Zed and lists its renderer commits
in its manifest — `EdgeFade`, `BackdropBlur`, GPU memory bounds,
destination-alpha fixes. Kael is a GPUI fork for the same reason.

Upstream will not remove the need. Zed moved Linux from Blade to wgpu in
PR #46758 and a maintainer stated on that PR that they have no plan to
replace the Windows or macOS renderers, because the native ones have better
performance and wider compatibility. The checkout agrees: only `gpui_linux`
and `gpui_web` depend on `gpui_wgpu`. GPUI keeps three shader languages.

Write-once therefore means **translation, not unification**. Naga reads WGSL
and writes MSL and HLSL, and its HLSL backend supports Shader Model 5.0,
which is what Direct3D 11 needs. The fork accepts WGSL, translates per
backend, and caches the result. Application code never sees MSL or HLSL.

The design is one new primitive and one new scene structure:

- **`EffectQuad`** — a quad whose fragment colour comes from a registered
  WGSL effect. What it samples is chosen by one field: nothing (the effect
  generates pixels), the captured content of a subtree, or the frame behind
  it. Three effect classes, one pipeline, one shader contract.
- **Nested scenes** — capturing a subtree means painting it into its own
  `Scene`, which the renderer draws to an offscreen texture with the code
  path it already has, then composites through an `EffectQuad`.

Backdrop effects need to read the frame. When a frame contains one, the
renderer draws that frame to an offscreen colour target and blits it at the
end. Frames without backdrop effects go straight to the swap chain and pay
nothing.

---

## 2. What GPUI actually does today

### 2.1 Eight primitives, sorted by overlap, batched by kind

A frame is a `Scene` holding one vector per primitive kind
(`gpui-0.2.2/src/scene.rs:24-35`):

```rust
pub(crate) struct Scene {
    pub(crate) paint_operations: Vec<PaintOperation>,
    primitive_bounds: BoundsTree<ScaledPixels>,
    layer_stack: Vec<DrawOrder>,
    pub(crate) shadows: Vec<Shadow>,
    pub(crate) quads: Vec<Quad>,
    pub(crate) paths: Vec<Path<ScaledPixels>>,
    pub(crate) underlines: Vec<Underline>,
    pub(crate) monochrome_sprites: Vec<MonochromeSprite>,
    pub(crate) polychrome_sprites: Vec<PolychromeSprite>,
    pub(crate) surfaces: Vec<PaintSurface>,
}
```

Every renderer consumes the same iterator, `Scene::batches()`
(`scene.rs:146`), which yields runs of one kind at one draw order. All three
`draw` functions are the same seven-arm match:
`gpui-0.2.2/src/platform/mac/metal_renderer.rs:419`,
`.../windows/directx_renderer.rs:286`,
`.../blade/blade_renderer.rs:643`.

### 2.2 Draw order is an overlap rank, not a timestamp

`BoundsTree::insert` returns `max_intersecting_ordering + 1`
(`gpui-0.2.2/src/bounds_tree.rs:94`) — one more than the highest order of
anything the new bounds overlap. **Two primitives that do not overlap can
carry the same order.** That is what lets GPUI batch so aggressively.

This rules out an obvious design. "Capture every primitive whose order falls
in a range" would sweep in unrelated primitives from elsewhere in the tree
that happen not to overlap. A capture needs an explicit structure, not an
order window.

### 2.3 Layers are ordering and clipping, and nothing reaches the renderer

`PaintOperation::StartLayer` / `EndLayer` exist (`scene.rs:192-195`) but
**no renderer reads `paint_operations`**. Grep the whole crate: the only
consumers are `Scene::replay` and `push_layer`/`pop_layer` themselves. The
renderer sees layers only through two effects: every primitive inside a
layer inherits that layer's single draw order (`scene.rs:67-80`), and each
primitive carries a rectangular `ContentMask` (`window.rs:1283-1286`).

Clipping is one axis-aligned rectangle per primitive. There is no mask
texture and no rounded clip. That is the same root cause the research note
records for children bleeding past rounded parents.

### 2.4 GPUI already renders to a texture, once, for paths

Paths are tessellated into an offscreen MSAA target, resolved, and sampled
back as sprites. Metal: `draw_paths_to_intermediate` at
`metal_renderer.rs:550` and `draw_paths_from_intermediate` at `:758`.
Direct3D: `directx_renderer.rs:398` and `:461`, with a full-size
intermediate plus a 4-sample MSAA companion (`:66-70`,
`PATH_MULTISAMPLE_COUNT = 4` at `:32`). Blade: `blade_renderer.rs:705`.

Every backend therefore already owns an intermediate colour target, a
pipeline that writes to it, and a pipeline that samples it. An effect layer
is the same machinery with a different consumer. This is the strongest
argument that the design belongs in GPUI rather than beside it.

### 2.5 The pixel format, and why it decides the colour rules

macOS and Windows both present **BGRA8 UNORM**, not sRGB —
`metal_renderer.rs:144` and `directx_renderer.rs:30`. The hardware does no
sRGB decode or encode, so GPUI blends sRGB-encoded values directly.

Blade does not fix its format. It takes whatever the surface negotiates
(`blade_renderer.rs:391`, `:398`, reading `surface.info().format`), which
blade allows to be either `Bgra8Unorm` or `Bgra8UnormSrgb`
(`blade-graphics-0.7.1/src/lib.rs:311-312`). Alpha is negotiated too: the
`premultiplied_alpha` global is set from `surface.info().alpha`
(`blade_renderer.rs:658-660`). **The colour rules are therefore not uniform
across backends**, which is precisely why they belong in the shader contract
rather than in each effect.

The blend state is Porter-Duff OVER on colour with additive alpha —
`SrcBlend = SRC_ALPHA`, `DestBlend = INV_SRC_ALPHA`, `SrcBlendAlpha = ONE`,
`DestBlendAlpha = ONE` (`directx_renderer.rs:1240-1246`; the same values at
`metal_renderer.rs:1206-1213`). Zeron's fork carries a commit that fixes
destination alpha on transparent windows for exactly this reason.

Two rules follow, and both are easy to get wrong:

- An effect that blurs or mixes must decide whether to work on the encoded
  values GPUI blends, or convert to linear, work, and convert back. Blurring
  encoded values darkens edges. Our effects convert, because a wrong blur is
  visible on the first screenshot.
- Any offscreen target must use BGRA8 UNORM and premultiplied alpha, and the
  composite back must use OVER. An offscreen target that stores straight
  alpha produces haloes wherever content meets transparency.

### 2.6 Text

Text reaches the GPU as sprites from the glyph atlas — `paint_glyph` at
`window.rs:2948` for monochrome, `paint_emoji` for colour. Shaping, fallback,
wrapping and the atlas are all upstream of the scene.

Subpixel anti-aliasing is the one place an effect can break text. GPUI already
disables it when the window is not opaque, because subpixel coverage is three
per-channel masks that cannot be composited over an unknown backdrop. An
effect layer has the same problem: its offscreen target has transparent
pixels. **Text inside an effect layer must render grayscale.** This is a real
loss and it is why an effect layer is the wrong tool for a body of text that
merely needs a tint.

---

## 3. Competing approaches

| # | Approach | Generative | Subtree | Backdrop | Animated | Cost |
|---|---|---|---|---|---|---|
| 1 | CPU render into `RenderImage` | yes | no | no | GPU→CPU→GPU each frame | none |
| 2 | Sibling wgpu surface behind a transparent window | yes | no | no | yes | low |
| 3 | `PaintSurface` / `CVPixelBuffer` | yes | no | no | yes | macOS only |
| 4 | Fork GPUI, add an effect primitive | yes | yes | yes | yes | high |
| 5 | Upstream the primitive to Zed | yes | yes | yes | yes | high, and slow |

**1 — CPU images.** `ImageSource::Render(Arc<RenderImage>)` takes BGRA bytes
and paints them as a polychrome sprite. It works everywhere and needs no
fork. It also costs a readback and an atlas upload per frame, which the brief
rules out for animated effects. Useful for a static generated texture; not a
system.

**2 — A sibling surface.** `Window` implements `HasWindowHandle`
(`gpui-0.2.2/src/window.rs:4845`) and we already use it
(`crates/inari-device-center/src/infrastructure/platform.rs:128-130`). We can
put a wgpu surface behind a transparent GPUI window and run any WGSL we like
on it. This is genuinely the cheapest way to get an authored, animated
background under the whole application, and it needs no fork at all. It
cannot do anything else: it never sees GPUI's pixels, so no subtree effect
and no backdrop. Keep it in the toolbox for a full-window atmosphere; it is
not the architecture.

**3 — Surfaces.** macOS only. Not a cross-platform answer.

**4 — Fork.** The only approach that covers all three effect classes.
Recommended. Section 4 is the design.

**5 — Upstream.** Worth doing after the fork proves the design, not before.
Zed's own position on PR #46758 is that they are conservative about renderer
changes, so this cannot be on the critical path.

### 3.1 Fork the monorepo, do not vendor the crate

GPUI is a subdirectory of a 197-crate workspace. Its manifest inherits 51
dependency entries from Zed's workspace root: 10 sibling crates and **41
third-party version pins**. Vendoring `crates/gpui` alone therefore means
carrying a manifest that cargo rewrote at publish time, and re-deriving that
rewrite on every update. Vendoring the siblings too does not help, because the
41 third-party inherits still resolve against a workspace table we would then
have to maintain by hand.

Fork the whole repository instead. Cargo "traverses the file tree to find
`Cargo.toml` for the requested crate anywhere inside the git repository", so a
git dependency on the monorepo resolves GPUI's workspace inheritance verbatim.
Nothing is vendored, no manifest is maintained, and an update is an ordinary
`git merge upstream/main` inside the fork. Zeron does this, and so does Zed for
its own 25 forked dependencies.

Branch from `69e2130`, the `gpui 0.2.2` release commit. Zed's `main` still
calls its crate `0.2.2` and is not the same API — `Application::new()` is gone,
and GPUI has split into `gpui`, `gpui_platform` and more, which
`gpui-component 0.5.1` cannot consume. A merge from `main` applies cleanly and
then fails to compile. Moving off `0.2.2` is a migration, not an update.

Two consequences worth knowing. `[patch.crates-io]` does not travel through a
git dependency, so any of Zed's own patches that GPUI needs must be repeated
here; at `69e2130` those are `notify`, `notify-types` and `windows-capture`,
and none of them reach GPUI in our feature set. And builds need the fork
cloned, which cargo does once into `CARGO_HOME` and shares.

---

## 4. The architecture

### 4.1 One primitive

```rust
pub(crate) struct EffectQuad {
    pub order: DrawOrder,
    pub bounds: Bounds<ScaledPixels>,
    pub content_mask: ContentMask<ScaledPixels>,
    pub corner_radii: Corners<ScaledPixels>,
    pub effect: EffectId,
    pub source: EffectSource,
    pub uniforms: [f32; 16],
}

pub(crate) enum EffectSource {
    /// The effect generates its own pixels. One pass, no offscreen target.
    Generated,
    /// Sample a subtree that was painted into its own scene.
    Captured(CaptureId),
    /// Sample the frame as it stands under this quad.
    Backdrop,
}
```

The three effect classes the brief distinguishes become three values of one
field. Every case runs the same vertex shader over the same instanced quad,
with the same corner-radius and content-mask handling GPUI already has for
`Quad`. The only difference is what texture is bound and what the effect is
allowed to read.

`uniforms` is a fixed 64-byte inline block. No per-effect buffer, no
allocation on the paint path, and animation is writing floats. Sixteen floats
is enough for a colour, a rect, a time and a handful of parameters; an effect
that needs more is telling us it should be two effects.

### 4.2 Capture is a nested scene

A subtree effect needs the subtree's pixels. Section 2.2 shows an order range
cannot identify a subtree. Index ranges into seven vectors could, but they
push the complexity into `BatchIterator`, which is the hottest code in the
frame.

Paint the subtree into its own `Scene`:

```rust
pub(crate) struct Scene {
    // …existing fields…
    pub(crate) effects: Vec<EffectQuad>,
    pub(crate) captures: Vec<Capture>,
}

pub(crate) struct Capture {
    pub id: CaptureId,
    /// Bounds plus the effect's outsets, in device pixels.
    pub bounds: Bounds<ScaledPixels>,
    pub scene: Scene,
}
```

`Window::with_effect_layer` swaps `self.next_frame.scene` for a fresh one,
runs the children, swaps back, and pushes a `Capture` plus the composite
`EffectQuad`. It is `paint_layer` (`window.rs:2782-2794`) with a different
body. Hitboxes, tooltips, input handlers and deferred draws live elsewhere in
`next_frame` and are untouched, so interaction inside an effect layer keeps
working.

The renderer gains one parameter:

```rust
fn draw_primitives(&mut self, scene: &Scene, target: &Target, viewport: Size<DevicePixels>)
```

Captures render first, depth-first, each into a pooled offscreen target sized
to its own bounds. Then the parent scene renders, and its `EffectQuad`s
sample the results. Recursion gives nested effects for free and gives them
the right meaning: an inner effect resolves before an outer one reads it.

**A capture is a stacking context.** Everything inside it shares one bounds
tree and one ordering universe, separate from its parent's. That matches CSS,
where `filter` also creates a stacking context, so it is explainable rather
than surprising. It is still a behaviour change for the subtree, and the
documentation has to say so.

### 4.3 Backdrop needs the frame in a texture

`EffectSource::Backdrop` samples what has already been drawn. No backend lets
a shader sample its own bound render target, and two of them will not let us
copy out of the swap chain at all: a `CAMetalLayer` defaults to
`framebufferOnly = YES` and GPUI never clears it (`metal_renderer.rs` sets
`maximum_drawable_count` at `:146` and nothing else), and a wgpu surface
texture carries `RENDER_ATTACHMENT` usage without `COPY_SRC`.

So: **when a scene contains a backdrop effect, render the frame to an
offscreen colour target and blit it to the swap chain at the end.** The
backdrop effect then samples that target, through a scratch copy of its own
region so it never reads and writes the same texture.

The scene knows whether it needs this before drawing starts, so the decision
is one boolean per frame. A frame with no backdrop effect renders straight to
the swap chain and pays nothing. A frame with one pays a single full-screen
blit.

This is the same shape as Zeron's fork, which allocates region-sized backdrop
scratch buffers and releases them when idle.

### 4.4 Multi-pass

A blur is not one pass. An effect declares its passes:

```rust
pub struct EffectDef {
    pub name: &'static str,
    pub wgsl: &'static str,
    pub passes: &'static [Pass],
    pub outsets: Outsets,
}

pub struct Pass {
    /// Fragment entry point in the WGSL module.
    pub entry: &'static str,
    /// 1 = full resolution, 2 = half, 4 = quarter.
    pub downsample: u32,
}
```

A dual-filter Gaussian is four passes: downsample to a quarter, blur
horizontally, blur vertically, then composite at full resolution. The
renderer runs passes into pooled scratch targets and hands the last one to
the composite.

`outsets` is how far the effect writes beyond its element bounds. A blur of
radius *r* needs roughly *3r*. The capture target and the composite quad both
grow by the outsets; layout does not. An effect that lies about its outsets
gets clipped edges, so the value belongs next to the shader that needs it.

### 4.5 The shader contract

Application WGSL is concatenated with a fixed preamble that declares
everything an effect may read. The preamble is the ABI, and the ABI is
reviewed like a public interface, because changing it recompiles every
effect.

```wgsl
struct EffectInput {
    /// 0..1 across the quad, including outsets.
    uv: vec2<f32>,
    /// Device pixels from the quad's top-left.
    position: vec2<f32>,
    /// Quad size in device pixels.
    size: vec2<f32>,
    /// Device pixels per logical pixel.
    scale: f32,
    /// Seconds since the window opened. Zero when motion is reduced.
    time: f32,
    /// The application's sixteen floats.
    params: array<f32, 16>,
}

/// Bound for Captured and Backdrop. Reading it under Generated is a
/// validation error, not a black texture.
@group(1) @binding(0) var t_source: texture_2d<f32>;
@group(1) @binding(1) var s_source: sampler;

/// Returns straight-alpha sRGB-encoded colour. The renderer premultiplies.
fn effect(input: EffectInput) -> vec4<f32>;
```

Straight alpha in the contract and premultiplication in the renderer is
deliberate. Premultiplied maths is where hand-written effects go wrong, and
one shared line of renderer code is easier to get right than every effect.

Two helpers ship in the preamble because Section 2.5 makes them mandatory
and nobody should reimplement them: `to_linear` and `to_encoded`.

### 4.6 WGSL to three backends

`EffectRegistry` is a GPUI global. The application registers effects at
startup. The renderer compiles them lazily, keyed by effect and pass, and
keeps the compiled pipeline for the process lifetime.

| Backend | Path | Compiled at |
|---|---|---|
| Blade / wgpu | WGSL straight through | runtime, by naga inside blade |
| Metal | naga `wgsl-in` → `msl-out` → `newLibraryWithSource` | runtime |
| Direct3D 11 | naga `wgsl-in` → `hlsl-out` at SM 5.0 → `D3DCompile` | runtime |

Runtime on all three matters more than it looks. It keeps effects in the
application crate, where they belong, instead of in the fork. The fork ships
a mechanism; Device Center ships the effects. GPUI's existing
`runtime_shaders` feature already proves the Metal half of this
(`metal_renderer.rs:156-162`).

Compilation failures must not be a runtime surprise. `naga`'s validator runs
over every registered effect in a unit test, so a broken shader fails
`mbx test` rather than a frame on a customer's machine.

---

## 5. The Rust API

Application code should never name a backend, a texture or a pass.

```rust
// Generative: a signature artifact that paints itself.
div()
    .size_full()
    .effect(Aurora { tint: theme.accent, drift: 0.4 })

// Subtree: everything inside is captured and composited through the effect.
card(theme)
    .effect_layer(Dissolve { progress })
    .child(readout("technical-details"))

// Backdrop: the frame behind this element, blurred.
div()
    .rounded(px(12.0))
    .backdrop(Frost { radius: px(24.0), tint: theme.surface })
```

An effect is a plain struct:

```rust
#[derive(Effect)]
#[effect(wgsl = "effects/frost.wgsl", outsets = "3 * radius")]
pub struct Frost {
    pub radius: Pixels,
    pub tint: Hsla,
}
```

The derive packs the fields into the sixteen floats and generates the field
offsets the WGSL preamble declares, so `params[0]` never has to be spelled by
hand on either side. Fields are ordinary values, so a spring or a
`fade_fraction` drives them the same way it drives any other number today.

Animation reuses what the crate already has. An animated effect keeps the
frame loop alive through `request_animation_frame` (`window.rs:1654`), and
`time` is forced to zero when `motion::reduced()` is set, so a reduced-motion
session gets the effect's resting frame rather than no effect at all.

Two things the API deliberately refuses. `.effect_layer` on text that only
needs a colour is a mistake, because it costs a texture and loses subpixel
anti-aliasing; the type system cannot stop it, so the documentation names it.
And there is no `.effect()` that takes a WGSL string literal — effects are
registered types, so the registry can validate them in a test.

---

## 6. Correctness checklist

Rendering work is not finished because it compiles. Each row is something to
confirm on screen, not in a review.

| Concern | Rule |
|---|---|
| Colour space | Targets are BGRA8 UNORM. Convert to linear for any mixing, convert back. |
| Alpha | Offscreen targets store premultiplied. Effects return straight. The renderer converts once. |
| Clipping | The composite quad honours `content_mask` and `corner_radii` exactly as `Quad` does. |
| Outsets | Capture targets and composite quads grow by the effect's outsets. Layout does not. |
| Nesting | Captures resolve depth-first. An inner effect is resolved before an outer one samples it. |
| Text | Grayscale anti-aliasing inside any capture. Shaping and the atlas are untouched. |
| DPI | Effects work in device pixels and receive `scale`. A blur radius in logical pixels is scaled once, by the renderer. |
| Resize | Pooled targets are invalidated on resize, as the path intermediates already are (`directx_renderer.rs:1095-1106`). |
| Device loss | Compiled pipelines are rebuilt by `handle_device_lost` (`directx_renderer.rs:209`). |
| Caching | Pipelines live for the process. Scratch targets are pooled by size and released when idle. |
| Budget | `INARI_GPU_STATS=1` reports captures, passes and scratch bytes per frame, following Zeron's `ZERON_GPU_STATS`. |

---

## 7. Performance

The cost of an effect should be readable from the call site.

- `.effect(…)` — one instanced quad. The same cost as a gradient. Use freely.
- `.backdrop(…)` — one region copy, *n* passes at reduced resolution, one
  composite, and one full-screen blit for the frame. The blit is paid once no
  matter how many backdrops there are.
- `.effect_layer(…)` — one offscreen target the size of the element plus
  outsets, a full re-render of the subtree into it, *n* passes, one
  composite. The most expensive, and the only one that can lose text quality.

The subtree case has an invalidation question the others do not: a capture
whose content did not change could be reused across frames. That is a real
optimisation and it is deliberately not in the first version, because it needs
a content hash on the nested scene and it is the kind of cache that produces
stale-pixel bugs. Measure first.

---

## 8. What to build first

Four effects, chosen because each one stresses a different part of the design
and would expose a different class of bug.

1. **Grain** — generative, one pass, no source texture. Proves the primitive,
   the uniform block, the WGSL-to-three-backends path, and nothing else. The
   application already wants it: low-opacity noise over flat fills is the
   cure for banding.
2. **Frost** — backdrop, four passes, downsampled. Proves the offscreen frame
   target, the region copy, multi-pass scratch pooling, outsets, and the
   colour-space rules. This is the effect
   [`device-center-glass-research.md`](device-center-glass-research.md) says
   we are missing.
3. **Dissolve** — subtree capture, one pass, animated. Proves nested scenes,
   grayscale text inside a capture, and that a captured subtree still takes
   clicks.
4. **Edge fade** — a scroll container's top and bottom fade. Proves the
   design handles the cheap case cheaply: it is a generative effect with an
   alpha ramp, and it should not allocate a texture. If our architecture makes
   this expensive, the primitive set is wrong.

Build them in that order. Each one works end to end before the next starts.

---

## 9. Risks

**The fork is permanent.** A patched `gpui` is maintained forever, and every
`gpui` upgrade becomes a rebase. Zeron accepted this and treats the fork's
commit list as a changelog in its manifest. We should do the same, and keep
the diff as small as the mechanism allows — no effect ever lands in the fork.

**Direct3D 11 is the floor.** Shader Model 5.0 has no wave intrinsics and
weaker resource binding than SM 6. An effect that compiles on Metal and
Vulkan can still fail on Windows. The validation test compiles every effect
for every backend, on every platform, so this is caught by `mbx test` and not
by a customer.

**Nested captures can multiply.** Nothing stops an effect layer inside an
effect layer inside a scroll list. The stats counter exists so the cost is
visible before it is a complaint.

**Text quality is the one irreversible loss.** Everything else in this design
degrades a frame's cost. A capture degrades how the text looks, on the one
platform where subpixel anti-aliasing still matters. Effect layers around
running text should stay rare and deliberate.

---

## 10. Status

Built and tested:

- `hadronomy/zed`, branch `inari/gpu-effects` — the fork, branched at `69e2130`
  and reached through `[patch.crates-io]`. This repository carries no Zed code.
- `crates/gpui/src/effect.rs` in the fork — the registry and the translator. One
  WGSL source reaches WGSL, MSL and HLSL at Shader Model 5.0, on binding slots
  stated once and asserted from both sides.
- `crates/gpui/src/effect/{preamble,epilogue}.wgsl` in the fork — the shader ABI.
- `crates/inari-device-center/src/ui/effect.rs` — the application's `Effect`
  trait and its catalogue, with tests that translate every effect we ship for
  every backend. `Grain` is the first entry.

Not built. Nothing draws yet. The renderer work is an `EffectQuad` primitive in
`scene.rs`, a `paint_effect` on `Window`, and a pipeline in each of
`platform/{mac,windows,blade}`, as specified in section 4. Until that lands,
`Grain` is a shader that provably compiles on three backends and reaches none of
them.

While iterating on the fork, override the pinned rev with a local checkout
rather than pushing for every build. Cargo supports `[patch]` in
`.cargo/config.toml` for exactly this — "local-only changes that you don't want
to commit".

Check the fork out in full. A sparse checkout of `crates/gpui` alone does not
work: cargo has to read the workspace root and GPUI's ten sibling crates to
resolve the manifest. The whole tree at `69e2130` is 3,560 files and 55 MB, so
there is nothing to save. The 509 MB figure people quote for Zed is history,
not working tree.
