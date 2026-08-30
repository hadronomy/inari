//! The effects this application paints, and the contract they are held to.
//!
//! An effect is WGSL plus a handful of floats. GPUI translates the WGSL for
//! whichever renderer is running — Metal, Direct3D or Blade — so an effect is
//! written once and nothing here names a backend. The mechanism and the reasons
//! it has to live in a GPUI fork are in
//! `docs/device-center-gpu-effects-architecture.md`.
//!
//! Adding an effect: write the WGSL beside this file, define a struct, and
//! implement [`Effect`]. The tests at the bottom then translate it for all three
//! backends, so a shader that would have been a black rectangle on a customer's
//! Windows machine is a failing `mbx test` here instead.

use gpui::effect::{self, EffectDef, EffectId, PARAM_COUNT};

/// Something the application can paint through a shader.
pub trait Effect: 'static {
    /// Name and source. The name is the registry key, so it must be unique and
    /// must not change once it has shipped.
    const DEF: EffectDef;

    /// The floats the shader reads back through `param(input, n)`.
    ///
    /// The indices here and the accessor functions at the top of the WGSL are
    /// two halves of one agreement that no compiler checks. Keep them adjacent
    /// and keep them short.
    // Only the tests call this until the renderer lands. `expect` rather than
    // `allow`, so the day `paint_effect` starts packing instances, the compiler
    // asks for this line back. It is scoped to non-test builds because the
    // tests do call it, and an `expect` that is fulfilled is itself an error.
    #[cfg_attr(not(test), expect(dead_code, reason = "no renderer draws an effect yet"))]
    fn params(&self) -> [f32; PARAM_COUNT];
}

/// The handle for an effect, registering it the first time it is asked for.
///
/// Cheap enough to call on every paint: registered effects take a read lock and
/// a scan over a list that has a handful of entries.
pub fn id<E: Effect>() -> EffectId {
    effect::register(E::DEF)
}

/// Film grain, to dither the banding out of large fills and long gradients.
///
/// The anti-slop rule this exists for: a flat or gradient surface on an 8-bit
/// target bands, and banding is the single most reliable tell that a surface was
/// filled by a computer rather than printed.
#[derive(Copy, Clone, Debug, PartialEq)]
pub struct Grain {
    /// Peak deviation, 0..1. Anything past about 0.05 stops being a substrate
    /// and starts being a texture.
    pub amount: f32,
    /// Grain cell size in logical pixels. One means per-pixel.
    pub size: f32,
    /// Offsets the pattern. Animate it for a moving grain; leave it for a still
    /// substrate.
    pub seed: f32,
}

impl Default for Grain {
    fn default() -> Self {
        Self { amount: 0.022, size: 1.0, seed: 0.0 }
    }
}

impl Effect for Grain {
    const DEF: EffectDef =
        EffectDef { name: "inari.grain", wgsl: include_str!("effect/grain.wgsl") };

    fn params(&self) -> [f32; PARAM_COUNT] {
        let mut params = [0.0; PARAM_COUNT];
        params[0] = self.amount;
        params[1] = self.size;
        params[2] = self.seed;
        params
    }
}

/// Register every effect the application owns.
///
/// Call this at startup so the renderer never has to compile a shader during the
/// first frame that draws it.
pub fn register_all() {
    id::<Grain>();
}

#[cfg(test)]
mod tests {
    use super::*;
    use gpui::effect::ShaderTarget;

    /// Exercises the parts of the ABI an effect is most likely to touch, so a
    /// change to the preamble that breaks one of them fails here rather than in
    /// whichever effect happens to use it.
    const SAMPLE: EffectDef = EffectDef {
        name: "inari.abi-sample",
        wgsl: r#"
fn effect(input: EffectInput) -> vec4<f32> {
    let tint = param_rgba(input, 0u);
    let amount = param(input, 4u);
    let edge = input.position / max(input.size, vec2<f32>(1.0));
    let mixed = mix(to_linear(tint.rgb), vec3<f32>(edge.x, edge.y, input.uv.x), amount);
    return vec4<f32>(to_encoded(mixed), tint.a * input.scale / max(input.scale, 1.0));
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
        // The reason this test exists: a shader that fails to translate is a
        // black rectangle on one platform and a stack trace on none of them.
        // Direct3D 11 is the strictest of the three and the one we cannot check
        // from a Mac any other way.
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
        // does not fail to compile. It draws the wrong pixels, which costs far
        // more to find.
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
    fn the_shader_and_the_cpu_agree_on_the_instance_layout() {
        // The failure this guards against is silent: WGSL and Rust align types
        // differently, so the two structs can drift apart while both still
        // compile, and the effect reads its parameters out of padding.
        assert_eq!(
            effect::shader_instance_size().unwrap() as usize,
            size_of::<effect::EffectInstance>(),
            "the shader and the CPU disagree about EffectInstance"
        );
    }

    #[test]
    fn a_broken_effect_names_itself() {
        // Line numbers alone are useless: every effect shares a preamble, so
        // "line 214" is the same line in all of them.
        const BROKEN: EffectDef = EffectDef {
            name: "inari.broken-sample",
            wgsl: "fn effect(input: EffectInput) -> vec4<f32> { return no_such_thing(); }",
        };
        let message = format!("{:#}", effect::translate(&BROKEN, ShaderTarget::Wgsl).unwrap_err());
        assert!(message.contains("inari.broken-sample"), "{message}");
    }

    #[test]
    fn an_effect_missing_its_function_fails_rather_than_drawing_nothing() {
        const EMPTY: EffectDef = EffectDef { name: "inari.empty-sample", wgsl: "// nothing here" };
        assert!(effect::translate(&EMPTY, ShaderTarget::Wgsl).is_err());
    }

    #[test]
    fn an_effect_keeps_one_handle_however_often_it_is_asked_for() {
        assert_eq!(id::<Grain>(), id::<Grain>());
    }

    #[test]
    fn grain_packs_its_parameters_where_its_shader_reads_them() {
        // The WGSL names these by index at the top of grain.wgsl. Nothing checks
        // the two agree, so this does.
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
    fn grain_rests_below_the_threshold_where_it_stops_being_a_substrate() {
        assert!(Grain::default().amount < 0.05, "grain would read as texture");
    }
}
