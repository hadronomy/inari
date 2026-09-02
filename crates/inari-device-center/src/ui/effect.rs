//! The effects this application paints, and the contract they are held to.
//!
//! An effect is a struct and a WGSL fragment function. Deriving [`gpui::effect::Effect`]
//! generates the accessors the shader calls from the field names, so neither
//! side spells a slot number. GPUI translates the WGSL for whichever renderer is
//! running, so nothing here names a backend.
//!
//! Adding an effect: write the WGSL beside this file, define a struct, derive
//! `Effect`. The tests then translate it for all three backends, so a shader
//! that would have been a black rectangle on a customer's Windows machine is a
//! failing `mbx test` here instead.

use gpui::effect::{Effect, ShaderEnum};
use gpui::{Hsla, IntoElement, Pixels, Point, effect_layer, px};

/// Film grain, to dither the banding out of large fills and long gradients.
///
/// A flat or gradient surface on an 8-bit target bands, and banding is the most
/// reliable sign that a surface was filled by a computer rather than printed.
#[derive(Effect, Copy, Clone, Debug, PartialEq)]
#[effect(name = "inari.grain", source = "effect/grain.wgsl")]
pub struct Grain {
    /// Peak deviation, 0..1. Past about 0.05 it stops being a substrate and
    /// starts being a texture.
    pub amount: f32,
    /// Grain cell size in logical pixels. One means per-pixel.
    pub size: f32,
    /// Offsets the pattern. Animate it for moving grain; leave it for a still
    /// substrate.
    pub seed: f32,
}

impl Default for Grain {
    fn default() -> Self {
        Self { amount: 0.022, size: 1.0, seed: 0.0 }
    }
}

/// A wall of pixel cells that blooms outward from a point and stays lit.
///
/// After Ryan Mulligan's pixel-canvas. Exists to exercise the effect path rather
/// than to ship: it animates, it reacts to input, and it uses both parameter
/// kinds, so a backend that gets any of that wrong shows it at a glance.
#[derive(Effect, Copy, Clone, Debug, PartialEq)]
#[effect(name = "inari.pixel-bloom", source = "effect/pixel_bloom.wgsl")]
pub struct PixelBloom {
    /// Seconds the wall has been on screen, for the idle breath of lit cells.
    pub time: f32,
    /// Grid spacing. A dot is a fraction of this, so the field reads as
    /// scattered points rather than as tiles.
    pub gap: Pixels,
    /// Where the bloom starts, from the wall's top-left.
    pub origin: Point<Pixels>,
    /// The largest a dot grows, as a fraction of its cell.
    pub dot_size: f32,
    /// Device pixels the bloom front travels per second.
    pub spread: f32,
    /// How fast an arrived dot oscillates its size.
    pub shimmer: f32,
    /// Strength of the halo outside each dot.
    pub glow: f32,
    /// Seconds since the pointer last entered or left.
    pub age: f32,
    /// What the pointer last did. Beside `age` rather than folded into its
    /// sign, because "never" and "left just now" are not the same state.
    pub pointer: Pointer,
    /// The colour of cells nearest the origin.
    pub near: Hsla,
    /// The colour cells drift towards, picked per cell rather than by distance,
    /// so the palette reads as a texture and not as a gradient.
    pub far: Hsla,
}

impl Default for PixelBloom {
    fn default() -> Self {
        Self {
            time: 0.0,
            gap: px(7.0),
            origin: Point::default(),
            dot_size: 0.4,
            spread: 1400.0,
            shimmer: 2.4,
            glow: 0.45,
            age: 0.0,
            pointer: Pointer::Never,
            near: gpui::blue(),
            far: gpui::blue(),
        }
    }
}

/// What the pointer has last done to a wall.
///
/// The shader branches on `pointer_is_inside(input)` and friends, generated
/// from these variants, so neither side spells a number.
#[derive(ShaderEnum, Copy, Clone, Debug, Eq, PartialEq)]
pub enum Pointer {
    /// The wall has never been pointed at, so nothing has bloomed.
    Never,
    /// The pointer is inside, and the bloom is running out from its origin.
    Inside,
    /// The pointer has left, and the field is unwinding in the order it arrived.
    Left,
}

/// A blur of whatever it is applied to, with a tint over the top.
///
/// The first effect that reads `source()`, so it is the one that proves a
/// filtered read survives translation to every backend.
#[derive(Effect, Copy, Clone, Debug, PartialEq)]
#[effect(name = "inari.frost", source = "effect/frost.wgsl")]
pub struct Frost {
    /// Blur radius in logical pixels.
    pub radius: f32,
    /// Laid over the blurred content; its alpha is how much glass there is.
    pub tint: Hsla,
}

/// Which way a separable pass runs.
///
/// A Gaussian is separable, so a blur is two passes over one axis each. Nothing
/// else is a direction, and an axis between the two is not a slower blur — it
/// is a value the shader would have to round.
#[derive(ShaderEnum, Copy, Clone, Debug, Eq, PartialEq)]
enum Axis {
    Across,
    Down,
}

/// A mark eroded into cracked, weathered stone.
///
/// For the one state that is not a failure but an absence: a component that was
/// never installed. A broken path is drawn with a cut wire, and a cut wire is
/// wrong for something that is simply not there.
#[derive(Effect, Copy, Clone, Debug, PartialEq)]
#[effect(name = "inari.weathered", source = "effect/weathered.wgsl")]
pub struct Weathered {
    /// How far gone, 0..1. Drives the cracks, the pitting and the bleaching
    /// together, so the state has one dial rather than three.
    pub amount: f32,
    /// Offsets the field. Two weathered marks on one screen are not the same
    /// stone unless they are given the same seed.
    pub seed: f32,
    /// What the stone bleaches towards. Its alpha is how much of it arrives.
    pub tint: Hsla,
}

impl Default for Weathered {
    fn default() -> Self {
        Self { amount: 0.68, seed: 0.0, tint: gpui::hsla(0.09, 0.06, 0.62, 0.55) }
    }
}

/// One axis of a Gaussian blur. Use [`blurred`]; this is half of it.
#[derive(Effect, Copy, Clone, Debug, PartialEq)]
#[effect(name = "inari.blur", source = "effect/blur.wgsl")]
struct Blur {
    /// The CSS `blur()` radius, which the shader halves to get a sigma.
    radius: Pixels,
    axis: Axis,
}

impl Blur {
    /// Three sigma: how far the kernel reads and how far the result spreads.
    /// Both are the same number, and it is what [`blurred`] hands each layer as
    /// its outset.
    fn reach(radius: Pixels) -> Pixels {
        radius * 1.5
    }
}

/// Blur `child`, the way `filter: blur(radius)` blurs a box on the web.
///
/// The child paints normally — real text, real glyphs, real everything — into a
/// texture, and the blur is applied to the result. It keeps its place in the
/// layout and its neighbours do not move; the blur spreads outside its bounds
/// the way a shadow does.
///
/// Two nested layers, because a Gaussian is separable: blurring across and then
/// down gives the same picture as one square kernel for a fraction of the reads,
/// and the fraction gets better the wider the blur. That the two compose without
/// anything special is the point — a capture is just a scene, so a capture of a
/// capture is too.
///
/// `radius` is the CSS number. Costs two textures the size of the child plus its
/// spread, so it is a thing to put on a glyph or a card, not on a scrolling list.
pub fn blurred(radius: Pixels, child: impl IntoElement) -> impl IntoElement {
    let outset = Blur::reach(radius);
    let across = Blur { radius, axis: Axis::Across };
    let down = Blur { radius, axis: Axis::Down };
    effect_layer(&down, effect_layer(&across, child).outset(outset)).outset(outset)
}

/// Register every effect the application owns.
///
/// Call this at startup so the renderer never has to compile a shader during the
/// first frame that draws one.
pub fn register_all() {
    gpui::effect::register(Grain::definition());
    gpui::effect::register(PixelBloom::definition());
    gpui::effect::register(Frost::definition());
    gpui::effect::register(Blur::definition());
    gpui::effect::register(Weathered::definition());
}

#[cfg(test)]
mod tests {
    use super::*;
    use gpui::effect::{self, EffectDef, ParameterDef, ParameterKind, ShaderTarget};

    /// Exercises the parts of the ABI an effect is most likely to touch, so a
    /// change to the preamble that breaks one of them fails here rather than in
    /// whichever effect happens to use it.
    const SAMPLE: EffectDef = EffectDef {
        name: "inari.abi-sample",
        parameters: &[
            ParameterDef { name: "tint", kind: ParameterKind::Color },
            ParameterDef { name: "blend", kind: ParameterKind::Scalar },
        ],
        wgsl: r#"
fn effect(input: EffectInput) -> vec4<f32> {
    let edge = input.position / max(input.size, vec2<f32>(1.0));
    let color = tint(input);
    let mixed = mix(to_linear(color.rgb), vec3<f32>(edge.x, edge.y, input.uv.x), blend(input));
    return vec4<f32>(to_encoded(mixed), color.a * input.scale / max(input.scale, 1.0));
}
"#,
    };

    /// An effect that reads what it was applied to, which is the half of the ABI
    /// a generative effect never touches.
    const OVER_SAMPLE: EffectDef = EffectDef {
        name: "inari.over-sample",
        parameters: &[ParameterDef { name: "amount", kind: ParameterKind::Scalar }],
        wgsl: r#"
fn effect(input: EffectInput) -> vec4<f32> {
    let shifted = source(input.uv + vec2<f32>(amount(input), 0.0));
    return mix(source(input.uv), shifted, 0.5);
}
"#,
    };

    const TARGETS: [ShaderTarget; 3] = [ShaderTarget::Wgsl, ShaderTarget::Msl, ShaderTarget::Hlsl];

    /// Every effect the application owns.
    fn catalogue() -> Vec<EffectDef> {
        register_all();
        effect::registered()
            .into_iter()
            .map(|(_, def)| def)
            .collect()
    }

    #[test]
    fn the_outset_is_the_distance_the_kernel_reads() {
        // A Gaussian is spent by three sigma, and CSS defines `blur(r)` as
        // sigma = r/2, so a blur reads and spreads 1.5r. Give the layer less
        // than that and the tail is cut off at the element's edge, which is
        // the exact artefact the outset exists to remove.
        for radius in [0.5, 2.0, 8.0, 40.0] {
            assert_eq!(
                Blur::reach(px(radius)),
                px(3.0 * (radius / 2.0)),
                "at radius {radius}"
            );
        }
    }

    /// An enum's constants, as generated code would emit them: one per variant,
    /// whether or not the shader branches on all of them.
    ///
    /// `Pointer` has three states and `pixel_bloom.wgsl` branches on two, so
    /// generating a constant per variant means shipping unused ones. HLSL turns
    /// a module constant into `static const`, and Shader Model 5.0 is the
    /// strictest target we have.
    const UNUSED_VARIANTS: EffectDef = EffectDef {
        name: "inari.unused-variants",
        parameters: &[ParameterDef {
            name: "mode",
            kind: ParameterKind::Enum(Pointer::VARIANTS),
        }],
        wgsl: r#"
fn effect(input: EffectInput) -> vec4<f32> {
    if mode_is_inside(input) {
        return vec4<f32>(1.0, 0.0, 0.0, 1.0);
    }
    return vec4<f32>(0.0);
}
"#,
    };

    #[test]
    fn a_variant_the_shader_never_names_still_translates() {
        for target in TARGETS {
            let source = effect::translate(&UNUSED_VARIANTS, target)
                .unwrap_or_else(|error| panic!("{target:?}: {error:#}"));
            if target == ShaderTarget::Hlsl {
                println!("--- HLSL for an enum with unused variants ---");
                for line in source.lines().filter(|line| line.contains("MODE_")) {
                    println!("{line}");
                }
                println!("--- end ---");
            }
        }
    }

    #[cfg(windows)]
    #[test]
    fn fxc_accepts_a_variant_the_shader_never_names() {
        // Decides whether generated enum constants can be emitted eagerly, one
        // per variant, or only for the variants a shader actually mentions.
        effect::validate_direct3d(&UNUSED_VARIANTS).unwrap();
    }

    #[test]
    fn an_axis_is_the_only_thing_a_blur_pass_can_run_along() {
        // The whole reason `axis` is not an `f32`: there is no third value for
        // it to hold, and the shader gets a name for each of the two.
        assert_eq!(Axis::VARIANTS.len(), 2);
        assert_eq!(Axis::Across.discriminant(), 0);
        assert_eq!(Axis::Down.discriminant(), 1);
    }

    #[test]
    fn the_blur_shader_branches_on_a_generated_predicate() {
        // Not on a number. `axis_is_down` is generated from the enum, so a
        // renamed variant fails to translate with the name in the message
        // instead of silently branching the other way.
        let wgsl = include_str!("effect/blur.wgsl");
        assert!(wgsl.contains("axis_is_down(input)"), "the blur spells its own axis");
    }

    #[test]
    fn the_wall_shader_branches_on_generated_predicates() {
        let wgsl = include_str!("effect/pixel_bloom.wgsl");
        for predicate in ["pointer_is_inside(input)", "pointer_is_never(input)"] {
            assert!(wgsl.contains(predicate), "the wall does not use `{predicate}`");
        }
        assert!(
            !wgsl.contains("const NEVER") && !wgsl.contains("const INSIDE"),
            "the wall still spells a discriminant by hand"
        );
    }

    #[test]
    fn the_blur_never_sums_straight_alpha() {
        // Averaging `source` drags the colour of transparent texels into the
        // result, and a transparent texel is black, so every blurred mark comes
        // out ringed in a dark halo. It looks like a bad blur rather than like a
        // wrong function, so nothing else would catch the swap back.
        let wgsl = include_str!("effect/blur.wgsl");
        assert!(
            !wgsl.contains("source("),
            "the blur reads straight alpha somewhere; it must sum premultiplied"
        );
        assert!(wgsl.contains("source_premultiplied("), "the blur reads nothing at all");
    }

    #[test]
    fn every_effect_we_ship_compiles_for_every_backend() {
        // A shader that fails to translate is a black rectangle on one platform
        // and a stack trace on none of them. Direct3D 11 is the strictest of the
        // three and the one a Mac cannot otherwise check.
        for def in catalogue() {
            for target in TARGETS {
                effect::translate(&def, target)
                    .unwrap_or_else(|error| panic!("`{}` for {target:?}: {error:#}", def.name));
            }
        }
    }

    #[cfg(windows)]
    #[test]
    fn fxc_accepts_every_effect_we_ship() {
        // `every_effect_we_ship_compiles_for_every_backend` proves naga emits
        // HLSL. fxc is the one that decides whether the pipeline gets built,
        // and it refuses things naga is happy to write — a register space, a
        // resource limit, an intrinsic Shader Model 5.0 does not have. The
        // renderer logs a rejected shader and draws nothing, so without this
        // the first report is a customer describing a blank rectangle.
        register_all();
        effect::validate_all_direct3d().unwrap();
    }

    #[test]
    fn the_abi_reaches_all_three_backends() {
        for target in TARGETS {
            let source = effect::translate(&SAMPLE, target)
                .unwrap_or_else(|error| panic!("{target:?}: {error:#}"));
            assert!(!source.is_empty(), "{target:?} produced nothing");
        }
    }

    #[test]
    fn the_entry_points_survive_translation() {
        for target in [ShaderTarget::Msl, ShaderTarget::Hlsl] {
            let source = effect::translate(&SAMPLE, target).unwrap();
            assert!(source.contains(effect::VERTEX_ENTRY), "no vertex entry for {target:?}");
            assert!(source.contains(effect::FRAGMENT_ENTRY), "no fragment entry for {target:?}");
        }
    }

    #[test]
    fn the_instance_buffer_lands_on_the_slots_the_renderers_bind() {
        // A mismatch between the translation's slots and the renderer's bindings
        // does not fail to compile. It draws the wrong pixels.
        use gpui::effect::slots;

        let hlsl = effect::translate(&SAMPLE, ShaderTarget::Hlsl).unwrap();
        assert!(
            hlsl.contains(&format!("register(t{})", slots::HLSL_INSTANCES_REGISTER)),
            "instances are not on t{}",
            slots::HLSL_INSTANCES_REGISTER
        );
        assert!(
            hlsl.contains(&format!("register(b{})", slots::HLSL_GLOBALS_REGISTER)),
            "globals are not on b{}",
            slots::HLSL_GLOBALS_REGISTER
        );

        let msl = effect::translate(&SAMPLE, ShaderTarget::Msl).unwrap();
        assert!(
            msl.contains(&format!("buffer({})", slots::MSL_INSTANCES_BUFFER)),
            "instances are not on Metal buffer {}",
            slots::MSL_INSTANCES_BUFFER
        );
    }

    #[test]
    fn an_effect_can_read_what_it_is_applied_to() {
        for target in TARGETS {
            let source = effect::translate(&OVER_SAMPLE, target)
                .unwrap_or_else(|error| panic!("{target:?}: {error:#}"));
            assert!(!source.is_empty(), "{target:?} produced nothing");
        }
    }

    #[test]
    fn the_source_texture_lands_on_the_slot_the_renderers_bind() {
        use gpui::effect::slots;

        let hlsl = effect::translate(&OVER_SAMPLE, ShaderTarget::Hlsl).unwrap();
        assert!(
            hlsl.contains(&format!("register(t{})", slots::HLSL_SOURCE_REGISTER)),
            "the source texture is not on t{}",
            slots::HLSL_SOURCE_REGISTER
        );
    }

    #[test]
    fn no_hlsl_reaches_fxc_with_a_register_space() {
        // naga emits samplers as a Direct3D 12 heap addressed with register
        // spaces, which is Shader Model 5.1 syntax that fxc rejects at 5.0
        // (gfx-rs/wgpu#8120). GPUI flattens it back to a plain binding, and this
        // is what proves the flattening still matches naga's output: the day it
        // stops, this fails here rather than on a customer's machine.
        for def in [SAMPLE, OVER_SAMPLE] {
            let hlsl = effect::translate(&def, ShaderTarget::Hlsl).unwrap();
            assert!(!hlsl.contains("nagaSamplerHeap"), "`{}` kept the sampler heap", def.name);
            assert!(
                !hlsl.contains(", space"),
                "`{}` kept a register space, which fxc rejects below 5.1",
                def.name
            );
        }
    }

    #[test]
    fn a_sampling_effect_gets_a_plain_sampler_binding() {
        use gpui::effect::slots;

        let hlsl = effect::translate(&OVER_SAMPLE, ShaderTarget::Hlsl).unwrap();
        assert!(
            hlsl.contains(&format!("register(s{})", slots::HLSL_SOURCE_SAMPLER_REGISTER)),
            "the sampler is not on s{}",
            slots::HLSL_SOURCE_SAMPLER_REGISTER
        );
    }

    #[test]
    fn a_shader_that_calls_an_undeclared_parameter_fails() {
        // The point of generating the accessors: a name the struct does not
        // declare does not exist in the module, so a stale shader is a
        // translation error rather than a wrong pixel.
        const STALE: EffectDef = EffectDef {
            name: "inari.stale-sample",
            parameters: &[ParameterDef { name: "amount", kind: ParameterKind::Scalar }],
            wgsl: "fn effect(input: EffectInput) -> vec4<f32> { return vec4<f32>(renamed(input)); }",
        };
        let message = format!("{:#}", effect::translate(&STALE, ShaderTarget::Wgsl).unwrap_err());
        assert!(message.contains("inari.stale-sample"), "{message}");
    }

    #[test]
    fn an_effect_missing_its_function_fails_rather_than_drawing_nothing() {
        const EMPTY: EffectDef =
            EffectDef { name: "inari.empty-sample", parameters: &[], wgsl: "// nothing here" };
        assert!(effect::translate(&EMPTY, ShaderTarget::Wgsl).is_err());
    }

    #[test]
    fn more_parameters_than_slots_is_reported_against_the_effect() {
        const TOO_MANY: EffectDef = EffectDef {
            name: "inari.too-many-sample",
            parameters: &[ParameterDef { name: "tint", kind: ParameterKind::Color }; 5],
            wgsl: "fn effect(input: EffectInput) -> vec4<f32> { return tint(input); }",
        };
        let message =
            format!("{:#}", effect::translate(&TOO_MANY, ShaderTarget::Wgsl).unwrap_err());
        assert!(message.contains("inari.too-many-sample"), "{message}");
    }

    #[test]
    fn the_shader_and_the_cpu_agree_on_the_instance_layout() {
        // WGSL and Rust align types differently, so the two structs can drift
        // apart while both still compile, and the effect then reads its
        // parameters out of padding.
        assert_eq!(
            effect::shader_instance_size().unwrap() as usize,
            size_of::<effect::EffectInstance>(),
            "the shader and the CPU disagree about EffectInstance"
        );
    }

    #[test]
    fn the_derive_packs_fields_in_declaration_order() {
        // The generated accessors read slot n; this is the other half of that
        // agreement, and the derive is what keeps them together.
        let params = effect::Params::of(&Grain { amount: 0.25, size: 3.0, seed: 7.0 });
        assert_eq!(params.slot(0), 0.25);
        assert_eq!(params.slot(1), 3.0);
        assert_eq!(params.slot(2), 7.0);
        assert_eq!(params.slot(3), 0.0, "a slot no field claimed is not zero");
    }

    #[test]
    fn the_derive_names_accessors_after_the_fields() {
        let names: Vec<_> = Grain::PARAMETERS
            .iter()
            .map(|p| p.name)
            .collect();
        assert_eq!(names, ["amount", "size", "seed"]);
    }

    #[test]
    fn grain_rests_below_the_threshold_where_it_stops_being_a_substrate() {
        assert!(Grain::default().amount < 0.05, "grain would read as texture");
    }
}

crate::story! {
    id: "effect.frost",
    name: "Frost",
    scope: crate::dev::Scope::Effects,
    about: "A blur over real content, beside the same card untouched. A capture \
            that never resolved looks exactly like the one on the right.",
    render: |dial, _window, _cx| {
        use gpui::{ParentElement as _, Styled as _};
        use gpui_component::StyledExt as _;
        use crate::ui::{content::Typography as _, theme::Theme};

        let radius = dial.range("Radius", 6.0, 0.0..=24.0);
        let card = |label: &'static str, radius: f32| {
            gpui::effect_layer(
                &Frost { radius, tint: gpui::rgba(0x6ea8fe22).into() },
                gpui::div()
                    .v_flex()
                    .gap(gpui::px(Theme::SPACE_SM))
                    .w(gpui::px(220.0))
                    .p(gpui::px(Theme::SPACE_LG))
                    .bg(gpui::rgb(0x1c1f26))
                    .child(gpui::div().text_body().child(label))
                    .child(gpui::div().text_caption().child(
                        "Small text is the honest test: a blur that is not running still reads.",
                    )),
            )
            .corner_radii(gpui::px(Theme::RADIUS_CARD))
        };

        gpui::div()
            .h_flex()
            .gap(gpui::px(Theme::SPACE_LG))
            .child(card("Blurred", radius))
            .child(card("Untouched", 0.0))
            .into_any_element()
    },
}

crate::story! {
    id: "effect.blur",
    name: "Blur",
    scope: crate::dev::Scope::Effects,
    about: "A separable Gaussian over real text. A glyph is the hardest thing \
            to blur: it is mostly edge, so a premultiplication mistake shows up \
            as a dark rim.",
    render: |dial, _window, _cx| {
        use gpui::{ParentElement as _, Styled as _};
        use gpui_component::StyledExt as _;
        use crate::ui::{content::Typography as _, theme::Theme};

        let single = dial.range("Radius", 6.0, 0.0..=24.0);
        let sample = || {
            gpui::div()
                .v_flex()
                .gap(gpui::px(Theme::SPACE_XS))
                .w(gpui::px(150.0))
                .child(gpui::div().text_body().child("Copied"))
                .child(gpui::div().text_caption().child(
                    "A halo here means the taps are summing straight alpha.",
                ))
        };
        let column = |radius: gpui::Pixels| {
            gpui::div()
                .v_flex()
                .gap(gpui::px(Theme::SPACE_SM))
                .child(
                    gpui::div()
                        .text_caption()
                        .child(format!("blur({}px)", f32::from(radius))),
                )
                .child(blurred(radius, sample()))
        };

        gpui::div()
            .v_flex()
            .gap(gpui::px(Theme::SPACE_XL))
            .child(column(gpui::px(single)))
            .child(
                gpui::div()
                    .h_flex()
                    .items_start()
                    .gap(gpui::px(Theme::SPACE_LG))
                    .children([0.0, 1.0, 2.0, 6.0, 16.0].map(|radius| column(gpui::px(radius)))),
            )
            .into_any_element()
    },
}

crate::story! {
    id: "effect.weathered",
    name: "Weathered",
    scope: crate::dev::Scope::Effects,
    about: "The mark as old stone. Shown at the size the gate draws it and again \
            enlarged: 40px hides whether the cracks are cracks.",
    render: |dial, _window, _cx| {
        use gpui::{ParentElement as _, Styled as _};
        use gpui_component::StyledExt as _;
        use crate::ui::{content::Typography as _, theme::Theme};

        let wear = dial.range("Wear", 0.68, 0.0..=1.0);
        let seed = dial.range("Seed", Weathered::default().seed, 0.0..=32.0);
        let mark = |wear: f32, edge: f32| {
            gpui::div()
                .v_flex()
                .items_center()
                .gap(gpui::px(Theme::SPACE_SM))
                .child(gpui::effect_layer(
                    &Weathered { amount: wear, seed, ..Weathered::default() },
                    gpui::svg()
                        .path("inari-mark-torii-ui.svg")
                        .size(gpui::px(edge))
                        .flex_none()
                        .text_color(gpui::rgb(0xb9b2a8)),
                ))
                .child(
                    gpui::div()
                        .text_caption()
                        .child(format!("{:.0}%", wear * 100.0)),
                )
        };

        gpui::div()
            .h_flex()
            .items_end()
            .gap(gpui::px(Theme::SPACE_XL))
            .children([0.0, 0.35, 0.68, 1.0].map(|step| mark(step, 40.0)))
            .child(mark(wear, 132.0))
            .into_any_element()
    },
}
