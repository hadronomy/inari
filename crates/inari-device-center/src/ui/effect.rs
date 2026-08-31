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

use gpui::Hsla;
use gpui::effect::Effect;

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
    /// Cell size in logical pixels.
    pub cell: f32,
    /// Where the bloom starts, in logical pixels from the wall's top-left.
    pub origin_x: f32,
    /// The other half of the origin.
    pub origin_y: f32,
    /// Seconds since the pointer last entered or left.
    pub age: f32,
    /// `1` while the pointer is inside, `-1` after it leaves, `0` before the
    /// wall has ever been pointed at. Two floats rather than a signed `age`,
    /// because "never" and "left just now" are not the same state.
    pub direction: f32,
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
            cell: 9.0,
            origin_x: 0.0,
            origin_y: 0.0,
            age: 0.0,
            direction: 0.0,
            near: gpui::blue(),
            far: gpui::blue(),
        }
    }
}

/// Register every effect the application owns.
///
/// Call this at startup so the renderer never has to compile a shader during the
/// first frame that draws one.
pub fn register_all() {
    gpui::effect::register(Grain::definition());
    gpui::effect::register(PixelBloom::definition());
}

#[cfg(test)]
mod tests {
    use super::*;
    use gpui::effect::{self, EffectDef, Parameter, ParameterKind, ShaderTarget};

    /// Exercises the parts of the ABI an effect is most likely to touch, so a
    /// change to the preamble that breaks one of them fails here rather than in
    /// whichever effect happens to use it.
    const SAMPLE: EffectDef = EffectDef {
        name: "inari.abi-sample",
        parameters: &[
            Parameter { name: "tint", kind: ParameterKind::Color },
            Parameter { name: "blend", kind: ParameterKind::Scalar },
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
    fn a_shader_that_calls_an_undeclared_parameter_fails() {
        // The point of generating the accessors: a name the struct does not
        // declare does not exist in the module, so a stale shader is a
        // translation error rather than a wrong pixel.
        const STALE: EffectDef = EffectDef {
            name: "inari.stale-sample",
            parameters: &[Parameter { name: "amount", kind: ParameterKind::Scalar }],
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
            parameters: &[Parameter { name: "tint", kind: ParameterKind::Color }; 5],
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
        let params = Grain { amount: 0.25, size: 3.0, seed: 7.0 }.params();
        assert_eq!(params[0], 0.25);
        assert_eq!(params[1], 3.0);
        assert_eq!(params[2], 7.0);
        assert!(
            params[3..]
                .iter()
                .all(|value| *value == 0.0)
        );
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
