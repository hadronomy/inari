// A box blur of whatever the effect was applied to, with a tint mixed over it.
//
// Exists to exercise the sampling half of the ABI: it is the first effect that
// reads `source()`, so it is the one that proves a filtered read survives
// translation to every backend. Outside an effect layer the source is a single
// transparent pixel, so the tint is all that shows — which makes "the shader
// compiled" and "the shader did not" look different on screen.

// Taps per axis. Nine is enough to look like a blur without being enough to
// matter for a test.
const TAPS: i32 = 4;

fn effect(input: EffectInput) -> vec4<f32> {
    let step = radius(input) * input.scale / max(input.size, vec2<f32>(1.0));

    var total = vec4<f32>(0.0);
    var weight = 0.0;
    for (var x = -TAPS; x <= TAPS; x++) {
        for (var y = -TAPS; y <= TAPS; y++) {
            let offset = vec2<f32>(f32(x), f32(y)) * step;
            // Triangular weights: a box blur twice is close enough to Gaussian
            // at this radius, and costs one multiply.
            let falloff = (1.0 - abs(f32(x)) / f32(TAPS + 1))
                * (1.0 - abs(f32(y)) / f32(TAPS + 1));
            total += source(input.uv + offset) * falloff;
            weight += falloff;
        }
    }

    let blurred = total / max(weight, 0.0001);
    let colour = tint(input);
    // Over the blurred content, not mixed with it, so the tint reads as a pane
    // of glass rather than as a wash.
    let over = colour.rgb * colour.a + to_linear(blurred.rgb) * (1.0 - colour.a);
    return vec4<f32>(to_encoded(over), max(blurred.a, colour.a));
}
