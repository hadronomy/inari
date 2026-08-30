// Film grain: very-low-amplitude noise laid over whatever is beneath it.
//
// Large flat fills and long gradients band on 8-bit targets. A little noise
// dithers the step so the eye reads a smooth surface. It should be felt and not
// seen, which is why the default amplitude is a few percent.

fn grain_amount(input: EffectInput) -> f32 { return param(input, 0u); }
fn grain_size(input: EffectInput) -> f32 { return param(input, 1u); }
fn grain_seed(input: EffectInput) -> f32 { return param(input, 2u); }

/// A cheap hash. Not uniform enough for anything that matters, and entirely
/// good enough for dither, which only needs the samples to be uncorrelated.
fn grain_hash(point: vec2<f32>) -> f32 {
    let scattered = fract(point * vec2<f32>(0.1031, 0.1030));
    let folded = scattered + dot(scattered, scattered.yx + 33.33);
    return fract((folded.x + folded.y) * folded.x);
}

fn effect(input: EffectInput) -> vec4<f32> {
    // Work in whole grain cells so the texture keeps a constant size on screen
    // instead of growing with the element or with the display scale.
    let cell = max(grain_size(input), 1.0) * input.scale;
    let point = floor(input.position / cell) + grain_seed(input);
    let noise = grain_hash(point) - 0.5;

    // Signed noise carried entirely in alpha, painted as mid grey. Over a dark
    // surface the positive half lifts and the negative half is invisible, which
    // is the wrong texture, so the sign rides the colour instead: black where
    // the noise is negative, white where it is positive.
    let amount = grain_amount(input) * abs(noise) * 2.0;
    let tone = step(0.0, noise);
    return vec4<f32>(vec3<f32>(tone), amount);
}
