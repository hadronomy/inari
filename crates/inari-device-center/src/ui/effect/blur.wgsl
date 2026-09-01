// One axis of a separable Gaussian blur. Two of these nested make the blur;
// see `blur::blurred`, which is the only thing that should build one.
//
// Separating the kernel is what makes a wide blur affordable: a 2D Gaussian of
// radius n costs n² reads per pixel, and two 1D passes cost 2n. Everything
// below is spent on making that 2n smaller still.
//
// Blurred in the encoded colour space rather than in linear light. That is
// deliberate on two counts: it is what CSS `filter: blur()` does, so a value
// lifted from a web design lands looking the way it did there, and it is what
// GPUI does everywhere else, so a blurred edge and the quad beside it agree.
// Converting each tap would also cost a `pow` per read, which is the most
// expensive thing in the loop by a wide margin.

/// Unnormalised Gaussian weight at `x` texels from centre.
///
/// The 1/(sigma*sqrt(2*pi)) out front is not computed, because every weight is
/// divided by their sum at the end and a constant factor cancels there.
fn gaussian(x: f32, sigma: f32) -> f32 {
    return exp(-(x * x) / (2.0 * sigma * sigma));
}

// Sample pairs per side. Each pair is one filtered read covering two texels, so
// this reaches sixteen — an exact kernel out to sigma 5.33 device pixels, which
// is a 21px blur on a 1x display or a 10px one at 2x. Past that the spacing
// stretches; see `step`.
const MAX_PAIRS: i32 = 8;

fn effect(input: EffectInput) -> vec4<f32> {
    // CSS defines `blur(r)` as a Gaussian with sigma = r/2, and r is the number
    // a designer hands over, so r is the parameter and the halving happens
    // here. The floor is a quarter texel: below half a texel a Gaussian is
    // indistinguishable from doing nothing, and it keeps the weights from
    // underflowing to a zero this would then divide by.
    let sigma = max(radius(input) * input.scale * 0.5, 0.25);

    // One texel along the axis being blurred, in uv.
    let direction = select(vec2<f32>(1.0, 0.0), vec2<f32>(0.0, 1.0), axis_is_down(input));
    let texel = direction / max(input.size, vec2<f32>(1.0));

    // Three sigma covers 99.7% of the kernel, and each pair reaches two texels,
    // so this is the count that covers it. Small radii cost proportionally
    // fewer reads rather than paying for taps whose weights round to nothing.
    let pairs = clamp(i32(ceil(sigma * 1.5)), 1, MAX_PAIRS);
    // Only ever above one when `pairs` hit the cap, and then it spreads the
    // reads we are allowed across the width the kernel actually needs. A wider
    // spacing undersamples, which on content this smooth reads as a slightly
    // softer blur rather than as banding — and the second pass is reading what
    // the first one already smoothed.
    let step = max(1.0, 3.0 * sigma / (2.0 * f32(pairs)));

    // Premultiplied throughout. Summing straight alpha drags the colour of
    // transparent texels into the result, and a transparent texel is black, so
    // that lands as a dark halo around everything the blur touches.
    var total = source_premultiplied(input.uv);
    var weight = 1.0;

    for (var k = 1; k <= pairs; k++) {
        let near = (2.0 * f32(k) - 1.0) * step;
        let far = 2.0 * f32(k) * step;
        let near_weight = gaussian(near, sigma);
        let far_weight = gaussian(far, sigma);
        // Two neighbouring taps read as one. Placing the sample between them,
        // at the point where their weights balance, makes the hardware's
        // bilinear filter perform the second read at no cost — which is what
        // halves the loop and why the sampler must be filtered.
        let pair_weight = near_weight + far_weight;
        let offset = (near * near_weight + far * far_weight) / pair_weight;

        total += source_premultiplied(input.uv + texel * offset) * pair_weight;
        total += source_premultiplied(input.uv - texel * offset) * pair_weight;
        weight += 2.0 * pair_weight;
    }

    // Back to the straight alpha the ABI returns. `source` would have done this
    // on the way in, one tap at a time, and been wrong for all of them.
    let blurred = total / weight;
    if blurred.a <= 0.0 {
        return vec4<f32>(0.0);
    }
    return vec4<f32>(blurred.rgb / blurred.a, blurred.a);
}
